"""Рейтинговый подбор: шаблон очереди, батчевый матчер и обратная связь комнаты (ADR 0038).

Матчер работает в процессе ``rooms`` под lease очереди ``(ladder, format)``. Один проход:
игроки очереди → жадные пары в окне обоих (``domain.queue``) → для каждой пары запись игроков
в ``matched`` (Lua под токеном lease: пара не создаётся дважды и после потери lease) → комната
в ``lobby`` с обоими игроками (готовы) → системная команда ``start`` хоста владельцу комнаты.
Дальше всё как у обычной дуэли: план game, отсчёт, матч. Подтверждения готовности нет.

Ladder, окна и интервалы приходят из проекции ``rating.LadderPublished``; в коде их нет.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import structlog
from prometheus_client import Counter, Gauge

from gv_core.clock import Clock
from gv_core.errors import AppError
from gv_core.random import Random
from gv_game_kernel import ConfigError
from match_service.application.owner import claim_room, new_code
from match_service.application.ports import (
    CommandBus,
    LadderInfo,
    ModeKeys,
    ModeSource,
    PlayerRooms,
    QueueFence,
    QueueRecord,
    QueueStore,
    RoomCommand,
    RoomStore,
    Users,
)
from match_service.application.rules import ModeTemplate, RulesFactory
from match_service.domain import errors
from match_service.domain.errors import MatchConflictError, MatchUnavailableError
from match_service.domain.model import Phase, Presence
from match_service.domain.queue import FORMAT_ANSWER, Candidate, pair_batch
from match_service.domain.room import Room, default_settings

log = structlog.get_logger(__name__)

_CODE_ATTEMPTS = 8
_SEND_ATTEMPTS = 2
PAIRS = Counter("gv_match_ranked_pairs_total", "Подобранные пары", ["ladder", "format"])
PAIR_FAILURES = Counter(
    "gv_match_ranked_pair_failures_total", "Пары, откатанные из-за сбоя", ["ladder", "format"]
)
SWEPT = Counter("gv_match_ranked_reserve_swept_total", "Зависшие резервы, возвращённые в поиск")
QUEUE_SIZE = Gauge("gv_match_queue_size", "Игроков в очереди", ["ladder", "format"])
DUEL = "duel"


class RankedTemplates:
    """Рейтинговый шаблон очереди: опубликованная дуэль ladder-а с нужным ``answer.format``.

    Найденное по ``mode_heads`` тело версии неизменяемо — разобранные шаблоны кэшируются.
    """

    def __init__(self, heads: ModeKeys, modes: ModeSource, factory: RulesFactory) -> None:
        self._heads = heads
        self._modes = modes
        self._factory = factory
        self._parsed: dict[tuple[str, int], ModeTemplate | None] = {}

    async def _template(self, key: str, version: int) -> ModeTemplate | None:
        cached = (key, version)
        if cached in self._parsed:
            return self._parsed[cached]
        body = await self._modes.body(key, version)
        if body is None:
            return None  # не кэшируем: тело может появиться позже
        try:
            parsed: ModeTemplate | None = self._factory.template(key, version, body)
        except (ConfigError, AppError) as exc:
            log.warning("match.ranked_template_invalid", mode_key=key, error=type(exc).__name__)
            parsed = None
        self._parsed[cached] = parsed
        return parsed

    async def resolve(self, ladder: LadderInfo, queue_format: str) -> ModeTemplate | None:
        """Шаблон для очереди или ``None`` (такой очереди пока нет).

        Raises:
            AppError: game недоступен при чтении тела шаблона.
        """
        answer = FORMAT_ANSWER.get(queue_format)
        if answer is None:
            return None
        for key, version in await self._heads.heads(ladder.domain):
            tpl = await self._template(key, version)
            if (
                tpl is not None
                and tpl.mode.ranked
                and tpl.mode.ladder == ladder.ladder
                and tpl.mode.kind == DUEL
                and tpl.template.answer_format == answer
            ):
                return tpl
        return None


@dataclass(frozen=True, slots=True)
class MatcherSettings:
    """Параметры матчера (``GV_MATCH__*``).

    Attributes:
        batch_max: Игроков очереди в батче (старейшие первыми).
        pending_room_ms: Свежий указатель на ещё не созданную комнату считается занятым.
        reserved_stale_ms: Резерв пары старше этого срока — матчер упал, не создав комнату.
    """

    batch_max: int
    pending_room_ms: int
    reserved_stale_ms: int = 10_000


@dataclass(slots=True)
class _Attempt:
    """Состояние одной попытки подбора пары: код меняется при коллизии, комната — создаётся."""

    code: str
    created: bool = False


class Matchmaker:
    """Один проход матчера по очереди под lease."""

    def __init__(
        self,
        *,
        queue: QueueStore,
        templates: RankedTemplates,
        player_rooms: PlayerRooms,
        store: RoomStore,
        bus: CommandBus,
        users: Users,
        clock: Clock,
        random: Random,
        settings: MatcherSettings,
    ) -> None:
        self._queue = queue
        self._templates = templates
        self._player_rooms = player_rooms
        self._store = store
        self._bus = bus
        self._users = users
        self._clock = clock
        self._random = random
        self._settings = settings

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    async def run_queue(self, ladder: LadderInfo, fmt: str, fence: QueueFence) -> int:
        """Подобрать пары очереди; вернуть число созданных комнат."""
        await self._sweep(ladder, fmt)
        size = await self._queue.count(ladder.ladder, fmt)
        QUEUE_SIZE.labels(ladder.ladder, fmt).set(size)
        if size < 2:  # noqa: PLR2004 — нужна пара
            return 0
        records = await self._queue.waiting(ladder.ladder, fmt, self._settings.batch_max)
        now_ms = self._now_ms()
        # Игроки на паузе после сорванного старта ждут (иначе комната в секунду - пункт 23).
        records = [r for r in records if r.retry_at_ms <= now_ms]
        if len(records) < 2:  # noqa: PLR2004
            return 0
        template = await self._templates.resolve(ladder, fmt)
        if template is None:
            log.warning("match.ranked_template_missing", ladder=ladder.ladder, format=fmt)
            return 0
        by_user = {r.user_id: r for r in records}
        pairs = pair_batch(
            [Candidate(r.user_id, r.rating, r.entered_at_ms) for r in records],
            ladder.matchmaking,
            self._now_ms(),
        )
        created = 0
        for first, second in pairs:
            try:
                if await self._match(
                    by_user[first.user_id], by_user[second.user_id], template, fence
                ):
                    created += 1
                    PAIRS.labels(ladder.ladder, fmt).inc()
            except Exception:
                # Пара уже возвращена в поиск (_match откатывает): остальные не страдают.
                PAIR_FAILURES.labels(ladder.ladder, fmt).inc()
                log.exception("match.ranked_pair_failed", ladder=ladder.ladder, format=fmt)
        return created

    # ------------------------------------------------------------------ зачистка
    async def _sweep(self, ladder: LadderInfo, fmt: str) -> None:
        """Резервы без комнаты (матчер упал между резервом и созданием): вернуть в поиск.

        Если комната успела создаться, а подтверждения нет — довести её: повторить ``start``
        (идемпотентно по ``op_id``) и подтвердить игрока.
        """
        cutoff = self._now_ms() - self._settings.reserved_stale_ms
        for record in await self._queue.stale_reserved(ladder.ladder, fmt, cutoff):
            code = record.room_code
            room = await self._store.load(code) if code else None
            if code is not None and room is not None and not room.is_closed:
                if room.phase is Phase.LOBBY:
                    await self._send_start(code, room.host_id)
                await self._queue.confirm(
                    record.ladder, record.format, record.user_id, record.user_id, code
                )
                continue
            if code is not None:
                await self._player_rooms.release(record.user_id, code)
                await self._queue.requeue(record.user_id, code)
            SWEPT.inc()
            log.warning("match.ranked_reserve_swept", ladder=ladder.ladder, format=fmt)

    # ------------------------------------------------------------------ пара
    async def _match(
        self, first: QueueRecord, second: QueueRecord, tpl: ModeTemplate, fence: QueueFence
    ) -> bool:
        attempt = _Attempt(new_code(self._random))
        users = (first.user_id, second.user_id)
        if not await self._queue.reserve(fence, *users, attempt.code, self._now_ms()):
            return False  # игрок вышел или lease потерян
        claimed: list[UUID] = []
        try:
            busy = await self._claim_both(users, attempt.code, claimed)
            if busy:
                # Игрок за это время занял другую комнату: он выбывает из очереди, соперник
                # возвращается в поиск (право одной активной комнаты, D10).
                for user_id in busy:
                    await self._queue.remove(user_id)
                await self._rollback(claimed, attempt, users)
                return False
            await self._create_room(first, second, tpl, attempt)
            await self._send_start(attempt.code, first.user_id)
            if not await self._queue.confirm(
                first.ladder, first.format, first.user_id, second.user_id, attempt.code
            ):
                log.warning("match.ranked_confirm_partial", room=attempt.code)
        except BaseException:
            await self._rollback(claimed, attempt, users)
            raise
        log.info(
            "match.ranked_room_created",
            room=attempt.code,
            ladder=first.ladder,
            format=first.format,
        )
        return True

    async def _send_start(self, code: str, host_id: UUID) -> None:
        """Системная команда ``start`` хоста; один повтор при сбое шины (``op_id`` тот же)."""
        command = RoomCommand(op_id=f"ranked-start-{code}", kind="start", user_id=host_id, data={})
        for attempt in range(_SEND_ATTEMPTS):
            try:
                await self._bus.send(code, command)
            except Exception:
                if attempt == _SEND_ATTEMPTS - 1:
                    raise
                log.warning("match.ranked_start_retry", room=code)
            else:
                return

    async def _claim_both(
        self, users: Sequence[UUID], code: str, claimed: list[UUID]
    ) -> list[UUID]:
        busy: list[UUID] = []
        for user_id in users:
            try:
                await claim_room(
                    self._player_rooms,
                    self._store,
                    user_id,
                    code,
                    now_ms=self._now_ms(),
                    pending_ms=self._settings.pending_room_ms,
                )
            except MatchConflictError:
                busy.append(user_id)
            else:
                claimed.append(user_id)
        return busy

    async def _rollback(
        self, claimed: Sequence[UUID], attempt: _Attempt, users: Sequence[UUID]
    ) -> None:
        """Откат попытки: недозапущенная комната убирается, указатели освобождаются, игроки
        возвращаются в поиск (под **актуальным** кодом — он мог смениться при коллизии)."""
        if attempt.created:
            try:
                await self._store.discard(attempt.code)
            except Exception:
                # Лобби без игроков закроется по простою: не мешаем откату остальных.
                log.exception("match.ranked_discard_failed", room=attempt.code)
        for user_id in claimed:
            await self._player_rooms.release(user_id, attempt.code)
        for user_id in users:
            await self._queue.requeue(user_id, attempt.code)

    async def _create_room(
        self, first: QueueRecord, second: QueueRecord, tpl: ModeTemplate, attempt: _Attempt
    ) -> None:
        """Комната дуэли в лобби: хост — дольше ждущий, второй игрок уже готов."""
        names = await self._users.nicknames([first.user_id, second.user_id])
        now = self._now_ms()
        for _ in range(_CODE_ATTEMPTS):
            room = Room.create(
                code=attempt.code,
                mode=tpl.mode,
                template=tpl.template,
                settings=default_settings(tpl.template),
                host_id=first.user_id,
                nickname=names.get(first.user_id, ""),
                now_ms=now,
            )
            room.join(second.user_id, names.get(second.user_id, ""), now)
            room.set_ready(second.user_id, ready=True, now_ms=now)
            room.pull_changes()
            if await self._store.create(room):
                attempt.created = True
                return
            # Код занят (редкая коллизия): новый код, указатели и записи очереди — за ним.
            fresh = new_code(self._random)
            users = (first.user_id, second.user_id)
            await self._queue.set_room(users, attempt.code, fresh)
            for user_id in users:
                await self._player_rooms.assign(user_id, fresh, now)
            attempt.code = fresh
        raise MatchUnavailableError("не удалось выдать код рейтинговой комнаты")


@dataclass(frozen=True, slots=True)
class StartFailurePolicy:
    """Что делать с игроками, когда старт рейтинговой комнаты сорван (``start_failed``).

    Attributes:
        backoff_ms: Пауза после первого срыва; каждый следующий удваивает её.
        backoff_max_ms: Потолок паузы.
        max_attempts: После стольких срывов подряд поиск прекращается с ошибкой
            ``match.start_unavailable`` (без нового круга комнат).
        notice_ttl_s: Сколько живёт сообщение об ошибке для игрока, который не опрашивает очередь.
    """

    backoff_ms: int = 2000
    backoff_max_ms: int = 30_000
    max_attempts: int = 5
    notice_ttl_s: int = 60

    def delay_ms(self, failures: int) -> int:
        """Пауза после ``failures``-го срыва подряд: ``backoff × 2^(n-1)``, не больше потолка."""
        return min(self.backoff_max_ms, self.backoff_ms << max(0, failures - 1))


class QueueHooks:
    """``RankedHooks``: комната сообщает очереди о старте матча и срыве отсчёта."""

    def __init__(
        self,
        queue: QueueStore,
        *,
        clock: Clock,
        policy: StartFailurePolicy | None = None,
    ) -> None:
        self._queue = queue
        self._clock = clock
        self._policy = policy or StartFailurePolicy()

    async def started(self, room: Room) -> None:
        """Матч идёт: записи очереди обоих игроков больше не нужны."""
        for seat in room.seats:
            await self._queue.release(seat.user_id, room.code)

    async def aborted(self, room: Room, survivors: Sequence[UUID]) -> None:
        """Срыв отсчёта: оставшиеся — в поиск с прежним ``entered_at``, ушедшие — прочь.

        Если комната закрыта из-за отказа старта (``start_error``), игроков не гоняем по кругу
        «комната - срыв - комната» каждую секунду: пауза растёт вдвое, а после лимита поиск
        прекращается с понятной ошибкой (пункт 23 замечаний тестирования).
        """
        failed_start = room.start_error is not None
        for seat in room.seats:
            if seat.user_id not in survivors or seat.presence is Presence.LEFT:
                await self._queue.release(seat.user_id, room.code)
            elif failed_start:
                await self._requeue_after_failed_start(seat.user_id, room.code)
            else:
                # Иной срыв (выход соперника) счётчик срывов старта не обнуляет: лимит не обойти.
                record = await self._queue.get(seat.user_id)
                await self._queue.requeue(
                    seat.user_id,
                    room.code,
                    failures=record.start_failures if record is not None else 0,
                )

    async def _requeue_after_failed_start(self, user_id: UUID, code: str) -> None:
        record = await self._queue.get(user_id)
        failures = (record.start_failures if record is not None else 0) + 1
        if failures >= self._policy.max_attempts:
            await self._queue.release(user_id, code)
            await self._queue.fail(user_id, errors.START_UNAVAILABLE, self._policy.notice_ttl_s)
            log.warning("match.ranked_start_gave_up", user_id=str(user_id), failures=failures)
            return
        now_ms = int(self._clock.now().timestamp() * 1000)
        retry_at = now_ms + self._policy.delay_ms(failures)
        await self._queue.requeue(user_id, code, failures=failures, retry_at_ms=retry_at)
