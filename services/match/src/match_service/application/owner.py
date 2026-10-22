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

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog
from prometheus_client import Histogram

from gv_core.clock import Clock
from gv_core.errors import AppError
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
    RoomCommand,
    RoomStore,
    RoundPlanPort,
    RoundPlanRequest,
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
from match_service.domain.model import Clue, MatchPlayer, MatchState, Phase, Presence
from match_service.domain.room import ROOM_CODE_ALPHABET, ROOM_CODE_LENGTH, Room, SettingsChange

log = structlog.get_logger(__name__)

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

    @property
    def timing(self) -> Timing:
        """Параметры автомата."""
        return Timing(
            idle_ms=self.idle_ms,
            presence_timeout_ms=self.presence_timeout_ms,
            memory_limit=self.memory_limit,
        )


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
        settings: OwnerSettings,
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
        """Комната ушла от этого владельца: правила её матча больше не нужны."""
        if room.match is not None:
            self._rules.pop(room.match.match_id, None)

    def _require_rules(self, room: Room) -> KernelMatchRules:
        rules = self.rules_for(room)
        if rules is None:
            raise MatchConflictError("в комнате нет матча", code=errors.PHASE_MISMATCH)
        return rules

    # ------------------------------------------------------------------ вход
    async def handle(self, room: Room, command: RoomCommand) -> Json:
        """Применить команду; повтор ``op_id`` — сохранённый ответ."""
        saved = room.op_reply(command.reply_key)
        if saved is not None:
            return saved
        started = time.perf_counter()
        handler = self._handlers.get(command.kind)
        try:
            if handler is None:
                raise MatchInvalidError("неизвестная команда", code="match.command_unknown")
            build = await handler(room, command, self._now())
            await self.settle(room)
            reply = ok(build())
        except AppError as exc:
            reply = failure(exc)
        finally:
            COMMAND_APPLY_SECONDS.labels(command.kind).observe(time.perf_counter() - started)
        if int(reply["status"]) < SERVER_ERROR:
            room.remember_op(command.reply_key, reply, self._settings.op_window)
        return reply

    async def tick(self, room: Room, seen: dict[UUID, int]) -> None:
        """Таймеры и присутствие (без команд)."""
        if room.is_closed:
            return
        match_fsm.presence(room, self.rules_for(room), self._timing, seen, self._now())
        await self.settle(room)

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
        await self.persist(room)
        if room.is_closed:
            for seat in room.seats:
                await self._player_rooms.release(seat.user_id, room.code)

    async def _resolve(
        self, room: Room, rules: KernelMatchRules, need: NeedClue | NeedFinalClue
    ) -> list[NeedClue | NeedFinalClue]:
        match = room.match
        if match is None:
            return []
        # Субъект выдачи — комната и раунд (D11): одна ссылка на (комната, раунд, уровень).
        subject = f"room:{room.code}:{need.round_index}"
        round_ = match.plan[need.round_index]
        try:
            if isinstance(need, NeedClue):
                view = await rules.clue(round_, need.level, assets=self._assets, subject=subject)
            else:
                view = await rules.final_clue(
                    round_, assets=self._assets, subject=f"{subject}:reveal"
                )
        except AppError as exc:
            # Ассет недоступен или media-pipeline лежит: уровень без ссылки — раунд void.
            log.warning("match.clue_unavailable", room=room.code, code=exc.code)
            view = None
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

    async def persist(self, room: Room) -> None:
        """Записать начало и итог матча (идемпотентно; отказ — повтор в следующем проходе)."""
        match = room.match
        if match is None:
            return
        try:
            if match.started_at_ms is not None and not match.started_written:
                event = match_started(room, match, to_dt(match.started_at_ms))
                await self._records.record_started(event, self._meta(room))
                match.started_written = True
            if match.finish is not None and not match.finished_written:
                await self._records.record_finished(room, match)
                match.finished_written = True
        except Exception:
            log.exception("match.persist_failed", room=room.code, match_id=str(match.match_id))

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

    async def _claim(self, room: Room, user_id: UUID) -> bool:
        """Занять ``player:{id}:room``; ``True`` — занято сейчас (откатить при ошибке)."""
        if await self._player_rooms.claim(user_id, room.code):
            return True
        other = await self._player_rooms.get(user_id)
        if other is None or other == room.code:
            await self._player_rooms.assign(user_id, room.code)
            return True
        other_room = await self._store.load(other)
        seat = other_room.seat_of(user_id) if other_room is not None else None
        if (
            other_room is None
            or other_room.is_closed
            or seat is None
            or seat.presence is Presence.LEFT
        ):
            await self._player_rooms.assign(user_id, room.code)
            return True
        raise MatchConflictError(
            "у вас уже есть активная комната",
            code=errors.ACTIVE_ROOM_CONFLICT,
            details={"code": other},
        )

    # ------------------------------------------------------------------ лобби
    async def _join(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        user = cmd.user_id
        seat = room.seat_of(user)
        fresh = seat is None or seat.presence is Presence.LEFT
        if room.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        claimed = await self._claim(room, user) if fresh else False
        try:
            room.join(user, str(cmd.data.get("nickname") or ""), now)
        except AppError:
            if claimed:
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
        await self._player_rooms.release(user, room.code)
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
        await self._player_rooms.release(target, room.code)
        return self._room_result(room)

    async def _transfer_host(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.transfer_host(cmd.user_id, UUID(str(cmd.data["user_id"])), now)
        return self._room_result(room)

    async def _reset_memory(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.reset_memory(cmd.user_id, now)
        return self._room_result(room)

    async def _start(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        room.require_startable(cmd.user_id)
        match_id = self._new_id()
        request = RoundPlanRequest(
            mode_key=room.mode.key,
            version=room.mode.version,
            seed=match_id.hex,
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
                    await self._player_rooms.assign(seat.user_id, code)
                room.rematch_code = code
                room.emit("lobby")
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
            me = my_round_view(room, cmd.user_id) or {
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
                "result": result.verdict.value,
                "attempts_left": left,
                "level_closed": left == 0,
                "version": room.version,
                "me": me,
            }

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
        await self._player_rooms.release(cmd.user_id, room.code)
        return self._version(room)

    async def _abort(self, room: Room, cmd: RoomCommand, now: int) -> Builder:
        """Принудительное завершение (админ): матч — ``aborted``, комната — ``closed``."""
        del cmd
        match_fsm.abort(room, self.rules_for(room), self._timing, "admin", now)
        return lambda: {"accepted": True, "version": room.version}
