"""Владелец комнаты: применение команд, таймеры, побочные эффекты (R2, R3, D5, D6, D11).

Работает в процессе ``rooms`` под lease комнаты (ADR 0034) и ничего не знает о Redis:
runner подаёт команды из потока и снимок, а после ``handle``/``tick`` сам пишет снимок,
публикует ``messages`` и отвечает отправителям. Здесь — права и правила:

* участие и права хоста проверяются здесь (application), API передаёт только субъекта;
* старт — один вызов ``RoundPlanPort`` (game), план — только в снимке;
* ссылка уровня — одна на комнату, раунд и уровень, в момент открытия (D11);
* итог — ``MatchRecords`` (Postgres + outbox, D8); повтор после handoff — no-op.

Ответ команды — словарь ``{"status": 200, "body": ...}`` или ошибка
``{"status", "code", "message", "details"}`` (``errors.error_for_status`` восстановит её
в процессе ``api``); ответы ≠ 5xx запоминаются в окне ``op_id``.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from uuid import UUID

import structlog
from prometheus_client import Counter, Histogram

from gv_core.clock import Clock
from gv_core.errors import AppError, GoneError
from gv_core.random import Random
from gv_game_kernel import (
    AnswerInput,
    AnswerKindError,
    AssetPort,
    ClueView,
    ConfigError,
    ContestRoundState,
    LevelLockedError,
    OptionNotFoundError,
    RoundFinishedError,
)
from match_service.application.ports import (
    Json,
    MatchRecords,
    PlayerRooms,
    RankedHooks,
    RoomCommand,
    RoomStore,
    RoundPlanPort,
    RoundPlanRequest,
    SpectatorRegistry,
    Subscriptions,
)
from match_service.application.rules import KernelMatchRules, RulesFactory, effective_body
from match_service.application.views import iso, my_round_view, room_view
from match_service.domain import errors, match_fsm
from match_service.domain.errors import (
    MatchConflictError,
    MatchForbiddenError,
    MatchInvalidError,
    MatchNotFoundError,
    MatchUnavailableError,
    status_of,
)
from match_service.domain.events import match_started
from match_service.domain.match_fsm import NeedClue, NeedFinalClue, Timing
from match_service.domain.model import (
    Clue,
    MatchPlayer,
    MatchState,
    Phase,
    PlayerFinish,
    Presence,
)
from match_service.domain.room import ROOM_CODE_ALPHABET, ROOM_CODE_LENGTH, Room, SettingsChange

log = structlog.get_logger(__name__)

RANKED_DROPS = Counter(
    "gv_match_ranked_countdown_dropped_total", "Игроки, сорвавшие отсчёт рейтинговой комнаты"
)
PERSIST_FAILURES = Counter(
    "gv_match_persist_failures_total", "Неудачные записи начала или итога матча в Postgres"
)
COMMAND_APPLY_SECONDS = Histogram(
    "gv_match_command_apply_seconds", "Применение команды владельцем комнаты", ["kind"]
)
SERVER_ERROR = 500

type Builder = Callable[[], Json]


_PLAN_ERRORS: dict[str, tuple[type[AppError], str]] = {
    "game.mode_not_found": (MatchNotFoundError, errors.MODE_NOT_FOUND),
    "game.mode_not_online": (MatchInvalidError, errors.MODE_NOT_ONLINE),
    "game.collection_not_found": (MatchNotFoundError, errors.COLLECTION_NOT_FOUND),
    "game.collection_domain_mismatch": (MatchNotFoundError, errors.COLLECTION_NOT_FOUND),
    "game.collection_required": (MatchInvalidError, errors.COLLECTION_REQUIRED),
    "game.collection_not_supported": (MatchInvalidError, errors.SETTING_NOT_ALLOWED),
    "game.too_many_collections": (MatchInvalidError, errors.TOO_MANY_COLLECTIONS),
    "game.pool_too_small": (MatchInvalidError, errors.POOL_TOO_SMALL),
    "game.assets_unavailable": (MatchUnavailableError, errors.CONTENT_UNAVAILABLE),
}


def plan_error(error: AppError) -> AppError:
    """Отказ плана game → ошибка match (комната остаётся в лобби, ADR 0033)."""
    if error.code.startswith("match."):
        return error
    mapped = _PLAN_ERRORS.get(error.code)
    if mapped is None:
        # Таймаут, breaker, 5xx и всё неизвестное: «онлайн временно недоступен».
        return MatchUnavailableError("онлайн временно недоступен", code=errors.GAME_UNAVAILABLE)
    cls, code = mapped
    details = dict(error.details) if error.details else None
    if code == errors.SETTING_NOT_ALLOWED:
        details = {"field": "collection_ids"}
    return cls("план матча не получен", code=code, details=details)


def _contest(match: MatchState) -> ContestRoundState:
    if match.contest is None:
        raise MatchConflictError("раунд не идёт", code=errors.PHASE_MISMATCH)
    return match.contest


def new_code(random: Random) -> str:
    """Код комнаты: 8 символов без 0/O/1/I (D12)."""
    return "".join(
        ROOM_CODE_ALPHABET[random.randrange(len(ROOM_CODE_ALPHABET))]
        for _ in range(ROOM_CODE_LENGTH)
    )


def to_dt(ms: int) -> datetime:
    """Миллисекунды → момент UTC."""
    return datetime.fromtimestamp(ms / 1000, UTC)


def clue_of(view: ClueView) -> Clue:
    """``ClueView`` ядра → ``Clue`` снимка."""
    return Clue(
        level=view.level,
        kind=view.kind,
        url=view.asset.url,
        expires_at_ms=int(view.asset.expires_at.timestamp() * 1000),
        mime=view.asset.mime,
        meta=dict(view.meta),
    )


@dataclass(frozen=True, slots=True)
class OwnerSettings:
    """Настройки владельца (``GV_MATCH__*``)."""

    op_window: int
    memory_limit: int
    idle_ms: int
    presence_timeout_ms: int
    poison_limit: int = 3
    op_users_max: int = 16
    pending_room_ms: int = 10_000
    empty_lobby_ms: int = 0

    @property
    def timing(self) -> Timing:
        """Параметры автомата."""
        return Timing(
            idle_ms=self.idle_ms,
            presence_timeout_ms=self.presence_timeout_ms,
            memory_limit=self.memory_limit,
            empty_lobby_ms=self.empty_lobby_ms,
        )


ROOM_RESULT_KINDS = frozenset(
    {"join", "leave", "ready", "settings", "kick", "transfer_host", "reset_memory", "start"}
)
"""Команды, чей ответ — ``RoomCommandResult`` (версия и вид комнаты)."""
CHAT_PHASES = frozenset({Phase.LOBBY, Phase.REVEAL, Phase.SUMMARY})
"""Свободный чат — только вне уровня: иначе подсказка ответа соперникам (аудит §7)."""
INTERNAL_ERROR = "match.unavailable"
PERSIST_BACKOFF_S = 0.5
PERSIST_BACKOFF_MAX_S = 30.0
POISON_REASON = "internal_error"


async def claim_room(
    player_rooms: PlayerRooms,
    store: RoomStore,
    user_id: UUID,
    code: str,
    *,
    now_ms: int,
    pending_ms: int,
) -> None:
    """Занять ``player:{id}:room`` под комнату ``code`` (одна активная комната, D10).

    Указатель на другую комнату мешает, если та не закрыта и игрок из неё не вышел (место
    есть и не ``left`` — или места ещё нет: вход в процессе), либо если комнаты ещё нет, а
    указатель свежее ``pending_ms`` (создание в процессе). Иначе указатель устарел и
    заменяется compare-and-set по старому значению: параллельный захват не потеряется.

    Raises:
        MatchConflictError: ``match.active_room_conflict``.
    """
    for _ in range(4):  # повтор — только если указатель поменялся между чтением и заменой
        if await player_rooms.claim(user_id, code, now_ms):
            return
        current = await player_rooms.pointer(user_id)
        if current is None:
            continue
        if current.code == code:
            return
        other = await store.load(current.code)
        if other is None:
            busy = now_ms - current.since_ms < pending_ms
        else:
            seat = other.seat_of(user_id)
            busy = not other.is_closed and (seat is None or seat.presence is not Presence.LEFT)
        if busy:
            raise MatchConflictError(
                "у вас уже есть активная комната",
                code=errors.ACTIVE_ROOM_CONFLICT,
                details={"code": current.code},
            )
        if await player_rooms.replace(user_id, current, code, now_ms):
            return
    raise MatchConflictError("у вас уже есть активная комната", code=errors.ACTIVE_ROOM_CONFLICT)


def is_transient(exc: BaseException) -> bool:
    """Сбой инфраструктуры (сеть, таймаут): команду повторить, а не пропускать."""
    return isinstance(exc, OSError | TimeoutError | ConnectionError)


def restore(room: Room, before: Room) -> None:
    """Вернуть комнате состояние ``before`` (копия до команды)."""
    for f in fields(Room):
        setattr(room, f.name, getattr(before, f.name))


def guess_body(room: Room, user_id: UUID, round_index: int, level: int, result: str) -> Json:
    """``GuessResult``: свой итог и оставшиеся попытки по текущему состоянию раунда."""
    me = my_round_view(room, user_id) or {
        "round_index": round_index,
        "status": None,
        "attempts_left": 0,
        "guesses": [],
        "options": [],
    }
    current = room.match
    still = (
        room.phase is Phase.LEVEL_OPEN
        and current is not None
        and current.round_index == round_index
        and current.contest is not None
        and current.contest.level == level
    )
    left = int(me["attempts_left"]) if still else 0
    return {
        "result": result,
        "attempts_left": left,
        "level_closed": left == 0,
        "version": room.version,
        "me": me,
    }


def compact(command: RoomCommand, reply: Json) -> Json:
    """Итог команды для окна ``op_id`` в снимке: статус, код, вердикт — без видов комнаты."""
    status = int(reply["status"])
    if status != 200:  # noqa: PLR2004 — HTTP OK
        out: Json = {"status": status, "code": reply.get("code")}
        if reply.get("details"):
            out["details"] = reply["details"]
        return out
    if command.kind == "guess":
        return {"status": status, "result": reply["body"]["result"]}
    return {"status": status}


def replay(room: Room, command: RoomCommand, saved: Json, now_ms: int) -> Json:
    """Ответ на повтор ``op_id`` по сохранённому итогу и текущему состоянию комнаты."""
    status = int(saved.get("status", 503))
    if status != 200:  # noqa: PLR2004 — HTTP OK
        return {
            "status": status,
            "code": saved.get("code"),
            "message": "повтор команды",
            "details": saved.get("details"),
        }
    kind = command.kind
    if kind in ROOM_RESULT_KINDS:
        return ok({"version": room.version, "room": room_view(room, now_ms)})
    if kind == "rematch":
        return ok(
            {
                "version": room.version,
                "room": room_view(room, now_ms),
                "rematch_code": room.rematch_code,
            }
        )
    if kind == "guess":
        data = command.data
        return ok(
            guess_body(
                room,
                command.user_id,
                int(data.get("round_index", 0)),
                int(data.get("level", 1)),
                str(saved.get("result")),
            )
        )
    if kind == "abort":
        return ok({"accepted": True, "version": room.version})
    return ok({"version": room.version})


def ok(body: Json) -> Json:
    """Успешный ответ команды."""
    return {"status": 200, "body": body}


def failure(error: AppError) -> Json:
    """Ответ-ошибка команды."""
    return {
        "status": status_of(error),
        "code": error.code,
        "message": error.message,
        "details": error.details or None,
    }


class RoomOwner:
    """Применение команд и таймеров к комнате (один экземпляр на процесс ``rooms``)."""

    def __init__(
        self,
        *,
        factory: RulesFactory,
        plans: RoundPlanPort,
        assets: AssetPort,
        records: MatchRecords,
        store: RoomStore,
        player_rooms: PlayerRooms,
        clock: Clock,
        random: Random,
        new_id: Callable[[], UUID],
        seeds: Callable[[], str],
        subscriptions: Subscriptions,
        settings: OwnerSettings,
        transient: Callable[[BaseException], bool] = is_transient,
        ranked: RankedHooks | None = None,
        spectators: SpectatorRegistry | None = None,
    ) -> None:
        self._factory = factory
        self._plans = plans
        self._assets = assets
        self._records = records
        self._store = store
        self._player_rooms = player_rooms
        self._clock = clock
        self._random = random
        self._new_id = new_id
        self._seeds = seeds
        self._subscriptions = subscriptions
        self._transient = transient
        self._ranked = ranked
        self._requeued: set[str] = set()
        self._spectators = spectators
        self._spectators_done: set[str] = set()
        self._failures: dict[str, tuple[str, int]] = {}
        self._tick_failures: dict[str, int] = {}
        self._persist_backoff: dict[str, tuple[int, int]] = {}
        self._settings = settings
        self._timing = settings.timing
        self._rules: dict[UUID, KernelMatchRules] = {}
        handlers: dict[str, Callable[[Room, RoomCommand, int], Awaitable[Builder]]] = {
            "join": self._join,
            "leave": self._leave,
            "ready": self._ready,
            "settings": self._change_settings,
            "kick": self._kick,
            "transfer_host": self._transfer_host,
            "reset_memory": self._reset_memory,
            "start": self._start,
            "rematch": self._rematch,
            "guess": self._guess,
            "skip": self._skip,
            "forfeit": self._forfeit,
            "emote": self._emote,
            "chat": self._chat,
            "away": self._away,
            "rename": self._rename,
            "remove": self._remove,
            "evict": self._evict,
            "abort": self._abort,
        }
        self._handlers = handlers

    def _now(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    # ------------------------------------------------------------------ правила
    def rules_for(self, room: Room) -> KernelMatchRules | None:
        """Правила текущего матча (из тела конфига в снимке; кэш по ``match_id``)."""
        match = room.match
        if match is None:
            return None
        rules = self._rules.get(match.match_id)
        if rules is None:
            rules = self._factory.build(match.body)
            self._rules[match.match_id] = rules
        return rules

    def forget(self, room: Room) -> None:
        """Комната ушла от этого владельца: правила, счётчики сбоев и паузы записи — тоже."""
        self._forget_rules(room)
        self._failures.pop(room.code, None)
        self._tick_failures.pop(room.code, None)
        self._persist_backoff.pop(room.code, None)
        self._requeued.discard(room.code)
        self._spectators_done.discard(room.code)

    def _forget_rules(self, room: Room) -> None:
        if room.match is not None:
            self._rules.pop(room.match.match_id, None)

    def _require_rules(self, room: Room) -> KernelMatchRules:
        rules = self.rules_for(room)
        if rules is None:
            raise MatchConflictError("в комнате нет матча", code=errors.PHASE_MISMATCH)
        return rules

    # ------------------------------------------------------------------ вход
    async def handle(self, room: Room, command: RoomCommand) -> Json:
        """Применить команду; повтор ``op_id`` — ответ по сохранённому итогу.

        Сбой не ``AppError`` откатывает комнату к состоянию до команды (иначе полупримененная
        команда ушла бы в снимок, а повтор ``op_id`` применил бы её второй раз). Сбой
        инфраструктуры (Redis, сеть) пробрасывается: runner перечитает снимок и повторит
        запись потока. Прочий сбой тоже пробрасывается, пока одна и та же команда не упала
        ``GV_MATCH__POISON_LIMIT`` раз подряд: тогда — ответ 503 ``match.unavailable`` и
        курсор идёт дальше (комната цела, команда пропущена).
        """
        saved = room.op_reply(command.user_id, command.op_id)
        if saved is not None:
            return replay(room, command, saved, self._now())
        started = time.perf_counter()
        handler = self._handlers.get(command.kind, self._unknown)
        before = copy.deepcopy(room)
        was_member = room.is_member(command.user_id)
        try:
            build = await handler(room, command, self._now())
            await self.settle(room)
            reply = ok(build())
        except AppError as exc:
            reply = failure(exc)
        except Exception as exc:
            restore(room, before)
            reply = self._crashed(room, command, exc)
        finally:
            COMMAND_APPLY_SECONDS.labels(command.kind).observe(time.perf_counter() - started)
        last = self._failures.get(room.code)
        if last is not None and last[0] == f"{command.user_id}:{command.op_id}":
            # Серия считается по одной записи потока: после перечитывания снимка прежние
            # команды пакета применяются заново и не должны обнулять её.
            self._failures.pop(room.code, None)
        # Окно op_id — только участникам (иначе посторонние раздувают снимок, аудит Н8);
        # добровольный выход тоже запоминается — его повтор должен вернуть тот же ответ.
        remembered = room.is_member(command.user_id) or (command.kind == "leave" and was_member)
        if int(reply["status"]) < SERVER_ERROR and remembered:
            room.remember_op(
                command.user_id,
                command.op_id,
                compact(command, reply),
                self._settings.op_window,
                self._settings.op_users_max,
            )
        return reply

    def _crashed(self, room: Room, command: RoomCommand, exc: Exception) -> Json:
        """Команда упала не ``AppError`` (комната уже откачена): повторить или пропустить."""
        if self._transient(exc):
            log.warning(
                "match.command_transient_failure",
                room=room.code,
                kind=command.kind,
                error_type=type(exc).__name__,
            )
            raise exc
        key = f"{command.user_id}:{command.op_id}"
        last, count = self._failures.get(room.code, ("", 0))
        count = count + 1 if last == key else 1
        log.exception("match.command_failed", room=room.code, kind=command.kind, attempt=count)
        if count < self._settings.poison_limit:
            self._failures[room.code] = (key, count)
            raise exc
        log.error("match.command_poisoned", room=room.code, kind=command.kind)
        return failure(MatchUnavailableError("сбой команды", code=INTERNAL_ERROR))

    async def tick(self, room: Room, seen: dict[UUID, int]) -> None:
        """Таймеры и присутствие; сбой инфраструктуры пробрасывается (runner повторит).

        Прочие сбои ``poison_limit`` тиков подряд — комната прерывается (``internal_error``);
        успешный тик обнуляет серию.
        """
        if room.is_closed:
            return
        try:
            match_fsm.presence(room, self.rules_for(room), self._timing, seen, self._now())
            await self.settle(room)
        except Exception as exc:
            if self._transient(exc):
                raise
            log.exception("match.tick_failed", room=room.code)
            await self._tick_failed(room)
        else:
            self._tick_failures.pop(room.code, None)

    async def _tick_failed(self, room: Room) -> None:
        """Сбой тика; ``poison_limit`` подряд — комната прерывается."""
        count = self._tick_failures.get(room.code, 0) + 1
        self._tick_failures[room.code] = count
        if count < self._settings.poison_limit or room.is_closed:
            return
        self._tick_failures.pop(room.code, None)
        log.error("match.room_poisoned", room=room.code, failures=count)
        try:
            rules = self.rules_for(room)
        except Exception:
            log.exception("match.rules_unavailable", room=room.code)
            rules = None
        try:
            match_fsm.abort(room, rules, self._timing, POISON_REASON, self._now())
        except Exception:
            log.exception("match.abort_failed", room=room.code)
            room.close(POISON_REASON, self._now())

    async def settle(self, room: Room) -> None:
        """Наступившие переходы, ссылки уровней, статусы, запись итога."""
        rules = self.rules_for(room)
        for _ in range(64):  # предохранитель: каждый проход открывает не больше раунда
            needs = match_fsm.advance(room, rules, self._timing, self._now())
            if not needs:
                break
            while needs and rules is not None:
                need = needs.pop(0)
                needs.extend(await self._resolve(room, rules, need))
        if rules is not None:
            match_fsm.refresh_statuses(room, rules)
        await self._ranked_abort(room)
        await self._drop_spectators(room)
        if room.is_closed or room.phase is Phase.LOBBY:
            # Матч сыгран (реванш, возврат в лобби, закрытие): правила больше не нужны —
            # иначе кэш владельца растёт с каждым матчем (ревью §10).
            self._forget_rules(room)
        if room.is_closed:
            for seat in room.seats:
                await self._player_rooms.release(seat.user_id, room.code)

    async def _drop_spectators(self, room: Room) -> None:
        """Матч закончился: подписки наблюдателей отзываются (как при кике).

        Токен наблюдателя выдаётся только идущему матчу, но подписка живёт до ``exp``:
        без отзыва наблюдатель читал бы итог и закрытую комнату дальше. Сбой — только лог.
        """
        if (
            self._spectators is None
            or room.phase not in (Phase.SUMMARY, Phase.CLOSED)
            or room.code in self._spectators_done
        ):
            return
        self._spectators_done.add(room.code)
        try:
            watchers = await self._spectators.drain(room.code)
        except Exception:
            log.exception("match.spectators_drain_failed", room=room.code)
            self._spectators_done.discard(room.code)
            return
        for user_id in watchers:
            await self._unsubscribe(user_id, room)

    async def _ranked_abort(self, room: Room) -> None:
        """Срыв отсчёта рейтинговой комнаты (ADR 0038): без штрафа, соперник — в очередь.

        Срыв — выход, сдача или отключение до конца отсчёта (``MatchStarted`` не было) либо
        отказ старта (план game). Комната закрывается, указатели игроков освобождаются
        (``settle``), оставшиеся возвращаются в поиск с прежним ``entered_at``. Матч в
        Postgres не пишется: матча не было.
        """
        if not room.mode.ranked or room.code in self._requeued:
            return
        match = room.match
        failed_start = room.start_error is not None
        dropped = match is not None and match.finish is not None and match.started_at_ms is None
        if not failed_start and not dropped:
            return
        gone = {
            p.user_id
            for p in (match.players.values() if match is not None else ())
            if p.finish in (PlayerFinish.LEFT, PlayerFinish.FORFEIT, PlayerFinish.TECH_LOSS)
        }
        survivors = [
            s.user_id
            for s in room.seats
            if s.presence is not Presence.LEFT and s.user_id not in gone
        ]
        self._requeued.add(room.code)
        for user_id in gone:
            # Для детектора аномалий этапа 5 (сталкинг очереди): штрафа нет, но срыв виден.
            RANKED_DROPS.inc()
            log.info("match.ranked_countdown_dropped", room=room.code, user_id=str(user_id))
        if not room.is_closed:
            room.close("start_failed" if failed_start else "countdown_aborted", self._now())
        if self._ranked is not None:
            try:
                await self._ranked.aborted(room, survivors)
            except Exception:
                # Записи очереди истекут сами; комната уже закрыта — не ломаем команду.
                log.exception("match.ranked_requeue_failed", room=room.code)

    async def _resolve(
        self, room: Room, rules: KernelMatchRules, need: NeedClue | NeedFinalClue
    ) -> list[NeedClue | NeedFinalClue]:
        match = room.match
        if match is None:
            return []
        # Субъект выдачи — комната и раунд (D11): одна ссылка на (комната, раунд, уровень).
        subject = f"room:{room.code}:{need.round_index}"
        round_ = match.plan[need.round_index]
        view: ClueView | None = None
        for attempt in range(2):  # один повтор: сбой media не должен сразу обнулять раунд
            try:
                if isinstance(need, NeedClue):
                    view = await rules.clue(
                        round_, need.level, assets=self._assets, subject=subject
                    )
                else:
                    view = await rules.final_clue(
                        round_, assets=self._assets, subject=f"{subject}:reveal"
                    )
                break
            except GoneError as exc:
                log.warning("match.clue_gone", room=room.code, code=exc.code)
                break
            except AppError as exc:
                log.warning(
                    "match.clue_unavailable", room=room.code, code=exc.code, attempt=attempt
                )
            except Exception:
                log.exception("match.clue_failed", room=room.code, attempt=attempt)
        clue = clue_of(view) if view is not None else None
        if isinstance(need, NeedFinalClue):
            match_fsm.set_final_clue(room, need.round_index, clue)
            return []
        return list(
            match_fsm.set_clue(
                room,
                rules,
                round_index=need.round_index,
                level=need.level,
                clue=clue,
                now_ms=self._now(),
            )
        )

    @staticmethod
    def unpersisted(room: Room) -> bool:
        """Итог матча ещё не в Postgres: комнату нельзя закрыть и отпустить (D8)."""
        match = room.match
        return match is not None and match.finish is not None and not match.finished_written

    async def persist(self, room: Room) -> None:
        """Записать начало и итог матча — после успешного снимка (вызывает runner).

        Идемпотентно (``ON CONFLICT`` по ``match_id``); отказ — метрика и повтор с
        экспоненциальной паузой, пока итог не записан (комната не закрывается).
        """
        match = room.match
        if match is None:
            return
        pending = (match.started_at_ms is not None and not match.started_written) or (
            match.finish is not None and not match.finished_written
        )
        if not pending:
            return
        failures, next_at = self._persist_backoff.get(room.code, (0, 0))
        if self._now() < next_at:
            return
        try:
            if match.started_at_ms is not None and not match.started_written:
                await self._ranked_started(room)
                event = match_started(room, match, to_dt(match.started_at_ms))
                await self._records.record_started(event, self._meta(room))
                match.started_written = True
            if match.finish is not None and not match.finished_written:
                await self._records.record_finished(room, match)
                match.finished_written = True
        except Exception:
            PERSIST_FAILURES.inc()
            log.exception("match.persist_failed", room=room.code, match_id=str(match.match_id))
            delay_ms = int(min(PERSIST_BACKOFF_MAX_S, PERSIST_BACKOFF_S * 2**failures) * 1000)
            self._persist_backoff[room.code] = (failures + 1, self._now() + delay_ms)
        else:
            self._persist_backoff.pop(room.code, None)

    async def _ranked_started(self, room: Room) -> None:
        """Отсчёт закончился: записи очереди обоих игроков больше не нужны."""
        if self._ranked is None or not room.mode.ranked:
            return
        try:
            await self._ranked.started(room)
        except Exception:
            log.exception("match.ranked_release_failed", room=room.code)

    @staticmethod
    def _meta(room: Room) -> Json:
        return {
            "title_key": room.mode.title_key,
            "clue_type": room.settings.clue_type,
            "answer_format": room.settings.answer_format,
        }

    # ------------------------------------------------------------------ ответы
    def _room_result(self, room: Room) -> Builder:
        return lambda: {"version": room.version, "room": room_view(room, self._now())}

    def _version(self, room: Room) -> Builder:
        return lambda: {"version": room.version}

    async def _unsubscribe(self, user_id: UUID, room: Room) -> None:
        """Отозвать подписку на канал комнаты (кик, выход, удаление); сбой — только лог."""
        try:
            await self._subscriptions.unsubscribe(user_id, f"room:{room.code}")
        except Exception:
            log.exception("match.unsubscribe_failed", room=room.code)

    async def _disconnect(self, user_id: UUID, room: Room, *, reason: str) -> None:
        """Разорвать соединения исключённого игрока (ADR 0059); сбой - только лог."""
        try:
            await self._subscriptions.disconnect(user_id, reconnect=True, reason=reason)
        except Exception:
            log.exception("match.disconnect_failed", room=room.code)

    async def _claim(self, room: Room, user_id: UUID) -> None:
        await claim_room(
            self._player_rooms,
            self._store,
            user_id,
            room.code,
            now_ms=self._now(),
            pending_ms=self._settings.pending_room_ms,
        )

    # ------------------------------------------------------------------ лобби
    async def _join(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        user = cmd.user_id
        seat = room.seat_of(user)
        fresh = seat is None or seat.presence is Presence.LEFT
        if room.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        if not fresh:
            match_fsm.rejoin(room, user, now)  # возврат по ссылке: снять паузу сразу
            return self._room_result(room)
        await self._claim(room, user)
        try:
            room.join(user, str(cmd.data.get("nickname") or ""), now)
        except AppError:
            await self._player_rooms.release(user, room.code)
            raise
        return self._room_result(room)

    async def _leave(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        user = cmd.user_id
        if room.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        room.require_seat(user)
        if room.in_match:
            match_fsm.leave(room, self._require_rules(room), self._timing, user, now)
        else:
            room.leave_lobby(user, now)
        # Окно op ушедшего не чистится: повтор leave с тем же op_id — тот же ответ
        # (правило 5); окно ограничено op_users_max и со временем вытесняется.
        await self._player_rooms.release(user, room.code)
        await self._unsubscribe(user, room)
        return self._room_result(room)

    async def _ready(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.set_ready(cmd.user_id, ready=bool(cmd.data.get("ready")), now_ms=now)
        return self._room_result(room)

    async def _change_settings(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        raw = dict(cmd.data.get("settings") or {})
        ids = cmd.data.get("collection_ids")
        change = SettingsChange(
            rounds=raw.get("rounds"),
            level_window_s=raw.get("level_window_s"),
            answer_format=raw.get("answer_format"),
            clue_type=raw.get("clue_type"),
            players_max=raw.get("players_max"),
            visibility=raw.get("visibility"),
            collection_ids=tuple(UUID(str(i)) for i in ids) if ids is not None else None,
        )
        room.change_settings(cmd.user_id, change, now)
        return self._room_result(room)

    async def _kick(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        target = UUID(str(cmd.data["user_id"]))
        room.kick(cmd.user_id, target, now)
        room.forget_ops(target)
        await self._player_rooms.release(target, room.code)
        await self._unsubscribe(target, room)
        await self._disconnect(target, room, reason="kicked")
        return self._room_result(room)

    async def _transfer_host(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.transfer_host(cmd.user_id, UUID(str(cmd.data["user_id"])), now)
        return self._room_result(room)

    async def _reset_memory(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.reset_memory(cmd.user_id, now)
        return self._room_result(room)

    async def _start(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.require_startable(cmd.user_id)
        try:
            return await self._start_match(room, now)
        except AppError as exc:
            # Ответ команды мог уже уйти 202: причина отказа — в лобби-дельте всем.
            room.fail_start(exc.code, now)
            raise

    async def _start_match(self, room: Room, now: int) -> Builder:
        match_id = self._new_id()
        request = RoundPlanRequest(
            mode_key=room.mode.key,
            version=room.mode.version,
            # Seed — секрет (только снимок): по известному seed и публичному сборнику
            # ответы вычислялись бы заранее (match_id видят клиенты).
            seed=self._seeds(),
            rounds=room.settings.rounds,
            collection_ids=tuple(room.settings.collection_ids),
            exclude_ids=tuple(room.memory),
        )
        try:
            plan = await self._plans.create_round_plan(request)
        except AppError as exc:
            raise plan_error(exc) from exc
        try:
            body = effective_body(plan.body, room.settings, room.template)
            rules = self._factory.build(body)
        except ConfigError as exc:
            log.warning("match.plan_config_invalid", room=room.code, details=exc.details)
            raise MatchInvalidError(
                "контент матча недоступен", code=errors.CONTENT_UNAVAILABLE
            ) from exc
        levels = range(1, rules.levels_count + 1)
        if any(r.asset_for(level) is None for r in plan.rounds for level in levels):
            raise MatchInvalidError("контент матча недоступен", code=errors.CONTENT_UNAVAILABLE)
        match = MatchState(
            match_id=match_id,
            seed=plan.seed,
            mode_version=plan.mode_version,
            body=body,
            plan=plan.rounds,
            collections=plan.collections,
            players={
                s.user_id: MatchPlayer(user_id=s.user_id, slot=s.slot)
                for s in room.seats
                if s.presence is not Presence.LEFT
            },
            created_at_ms=self._now(),
            grace_ms=rules.grace_ms,
            levels_count=rules.levels_count,
            attempts_per_level=rules.attempts_per_level,
        )
        self._rules[match_id] = rules
        match_fsm.begin(room, match, rules, self._now())
        del now
        return self._room_result(room)

    async def _rematch(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        if room.mode.ranked:
            # Рейтинговый матч — только из очереди (иначе реванш обходит подбор и
            # упрощает win-trading, ADR 0038).
            raise MatchForbiddenError(
                "рейтинговые матчи — только из очереди", code=errors.RANKED_FORBIDDEN
            )
        if match_fsm.vote_rematch(room, cmd.user_id, now):
            if room.template.options is not None:
                match_fsm.back_to_lobby(room, now)
            else:
                await self._new_room(room, now)
        return lambda: {
            "version": room.version,
            "room": room_view(room, self._now()),
            "rematch_code": room.rematch_code,
        }

    async def _new_room(self, room: Room, now: int) -> None:
        """Реванш дуэли и party: новая комната с теми же игроками и настройками."""
        players = [s for s in room.seats if s.presence is not Presence.LEFT]
        host = (
            room.host_id if any(s.user_id == room.host_id for s in players) else players[0].user_id
        )
        for _ in range(8):
            code = new_code(self._random)
            seats = sorted(players, key=lambda s: (s.user_id != host, s.slot))
            fresh = Room.create(
                code=code,
                mode=room.mode,
                template=room.template,
                settings=room.settings,
                host_id=host,
                nickname=seats[0].nickname,
                now_ms=now,
            )
            for seat in seats[1:]:
                fresh.join(seat.user_id, seat.nickname, now)
            fresh.memory = list(room.memory)
            fresh.pull_changes()
            if await self._store.create(fresh):
                for seat in players:
                    await self._player_rooms.assign(seat.user_id, code, now)
                # Старая комната закрывается: код реванша получают все (дельта closed и
                # RoomView.rematch_code в state/tick).
                room.rematch_code = code
                room.close("rematch", now)
                return
        raise MatchUnavailableError("не удалось создать комнату реванша")

    # ------------------------------------------------------------------ матч
    async def _guess(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        rules = self._require_rules(room)
        data = cmd.data
        round_index, level = int(data["round_index"]), int(data["level"])
        match_fsm.require_turn(
            room,
            cmd.user_id,
            round_index=round_index,
            level=level,
            expected_version=int(data["expected_version"]),
        )
        match = room.match
        assert match is not None  # noqa: S101 — require_turn проверил матч
        raw = dict(data["answer"])
        option = raw.get("option_id")
        item = raw.get("media_item_id")
        answer = AnswerInput(
            media_item_id=UUID(str(item)) if item is not None else None,
            text=str(raw["text"]) if raw.get("text") is not None else None,
            option_id=str(option) if option is not None else None,
        )
        try:
            step = rules.guess(
                _contest(match),
                str(cmd.user_id),
                answer,
                match.current,
                display=self._display(answer, match),
                now_ms=now,
            )
        except AnswerKindError as exc:
            raise MatchInvalidError(
                "режим не принимает такой ответ", code=errors.ANSWER_KIND_UNSUPPORTED
            ) from exc
        except OptionNotFoundError as exc:
            raise MatchInvalidError("такого варианта нет", code=errors.OPTION_UNKNOWN) from exc
        except (LevelLockedError, RoundFinishedError) as exc:
            raise MatchConflictError("уровень закрыт", code=errors.LEVEL_LOCKED) from exc
        result = step.match
        match_fsm.commit(room, rules, cmd.user_id, step.state, now)

        def build() -> Json:
            return guess_body(room, cmd.user_id, round_index, level, result.verdict.value)

        return build

    @staticmethod
    def _display(answer: AnswerInput, match: MatchState) -> str:
        if answer.text is not None:
            return answer.text.strip()[:120]
        if answer.option_id is not None:
            option = next(
                (o for o in match.current.options if o.option_id == answer.option_id), None
            )
            return (option.title or option.label_key or "") if option is not None else ""
        return ""  # выбор из автокомплита: название знает клиент, у match каталога нет

    async def _skip(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        rules = self._require_rules(room)
        data = cmd.data
        match_fsm.require_turn(
            room,
            cmd.user_id,
            round_index=int(data["round_index"]),
            level=int(data["level"]),
            expected_version=int(data["expected_version"]),
        )
        match = room.match
        assert match is not None  # noqa: S101 — require_turn проверил матч
        try:
            state = rules.skip(_contest(match), str(cmd.user_id), now)
        except (LevelLockedError, RoundFinishedError) as exc:
            raise MatchConflictError("уровень закрыт", code=errors.LEVEL_LOCKED) from exc
        match_fsm.commit(room, rules, cmd.user_id, state, now)
        return self._version(room)

    async def _forfeit(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        if room.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        room.require_seat(cmd.user_id)
        match_fsm.forfeit(room, self._require_rules(room), self._timing, cmd.user_id, now)
        await self._player_rooms.release(cmd.user_id, room.code)
        return self._version(room)

    async def _emote(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        if room.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        room.require_seat(cmd.user_id)
        kind, key = str(cmd.data.get("kind")), str(cmd.data.get("key"))
        chat = room.template.chat
        allowed = chat.emotes if kind == "emote" else chat.phrases if kind == "phrase" else ()
        if key not in allowed:
            raise MatchInvalidError("нет такой эмоции", code=errors.EMOTE_UNKNOWN)
        room.touch(now)
        room.emit(
            "emote",
            user_id=str(cmd.user_id),
            emote=key if kind == "emote" else None,
            phrase=key if kind == "phrase" else None,
            at=iso(now),
        )
        return self._version(room)

    async def _chat(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        if room.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        room.require_seat(cmd.user_id)
        if not room.template.chat.free_text:
            raise MatchForbiddenError(
                "в этой комнате нет свободного чата", code=errors.CHAT_DISABLED
            )
        if room.phase not in CHAT_PHASES:
            raise MatchInvalidError(
                "чат недоступен во время уровня",
                code=errors.CHAT_DISABLED,
                details={"reason": "phase"},
            )
        room.touch(now)
        room.emit(
            "chat",
            message_id=str(self._new_id()),
            user_id=str(cmd.user_id),
            text=str(cmd.data["text"]),
            at=iso(now),
        )
        return self._version(room)

    async def _away(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.set_away(cmd.user_id, away=bool(cmd.data.get("away")), now_ms=now)
        return self._version(room)

    async def _unknown(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        del room, now
        raise MatchInvalidError(
            "неизвестная команда", code=errors.COMMAND_UNKNOWN, details={"kind": cmd.kind}
        )

    # ------------------------------------------------------------------ системные
    async def _rename(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        del now
        room.rename(cmd.user_id, str(cmd.data["nickname"]))
        return self._version(room)

    async def _remove(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        """Аккаунт удалён (``identity.UserDeleted``): игрок покидает комнату."""
        seat = room.seat_of(cmd.user_id)
        if seat is not None and seat.presence is not Presence.LEFT and not room.is_closed:
            if room.in_match:
                match_fsm.leave(room, self._require_rules(room), self._timing, cmd.user_id, now)
            else:
                room.leave_lobby(cmd.user_id, now)
        if seat is not None:
            seat.nickname = ""  # ПДн удалённого аккаунта не остаются в снимке
            # Команда приходит из транзакции consumer'а до её коммита: итог матча не может
            # полагаться только на users_proj.deleted (аудит §5).
            room.forgotten.add(cmd.user_id)
        room.forget_ops(cmd.user_id)
        await self._player_rooms.release(cmd.user_id, room.code)
        await self._unsubscribe(cmd.user_id, room)
        return self._version(room)

    async def _evict(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        """Санкция ``ONLINE`` (``social.UserSanctionsChanged``, ADR 0052): игрок покидает комнату.

        Как ``leave``, но по системной команде consumer'а: ник и история остаются (в отличие
        от ``remove`` при удалении аккаунта); подписка Centrifugo отзывается с повтором.
        """
        seat = room.seat_of(cmd.user_id)
        if seat is not None and seat.presence is not Presence.LEFT and not room.is_closed:
            if room.in_match:
                match_fsm.leave(room, self._require_rules(room), self._timing, cmd.user_id, now)
            else:
                room.leave_lobby(cmd.user_id, now)
        room.forget_ops(cmd.user_id)
        await self._player_rooms.release(cmd.user_id, room.code)
        await self._unsubscribe(cmd.user_id, room)
        return self._version(room)

    async def _abort(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        """Принудительное завершение (админ): матч — ``aborted``, комната — ``closed``."""
        del cmd
        match_fsm.abort(room, self.rules_for(room), self._timing, "admin", now)
        return lambda: {"accepted": True, "version": room.version}
