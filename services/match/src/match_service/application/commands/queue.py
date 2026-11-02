"""Очередь рейтинговой дуэли на стороне api (ADR 0038): enter / tick / leave / pool.

Допуск — любой зарегистрированный аккаунт без санкции ``RANKED``. Рейтинг для подбора берётся
из проекции ``rating.RatingChanged`` (новичок — стартовый рейтинг ladder-а), синхронных
вызовов rating нет. Игрок состоит ровно в одной очереди и не может иметь активную комнату.
Матчер (процесс ``rooms``) переводит запись в ``matched`` с кодом комнаты; клиент узнаёт об
этом по ``tick``.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError
from gv_db.idempotency import request_fingerprint
from match_service.application.commands.rooms import Actor, effective_actor
from match_service.application.matchmaking import RankedTemplates
from match_service.application.owner import ActiveRoom, active_room_of
from match_service.application.ports import (
    CommandBus,
    IdempotentCreates,
    Json,
    LadderInfo,
    LadderProjection,
    PlayerRooms,
    PresenceStore,
    QueueRecord,
    QueueStore,
    RateLimiter,
    RoomCommand,
    RoomStore,
)
from match_service.application.sanctions import SanctionGate, online_banned
from match_service.application.views import iso
from match_service.domain import errors
from match_service.domain.errors import (
    MatchConflictError,
    MatchForbiddenError,
    MatchInvalidError,
    MatchNotFoundError,
    MatchUnavailableError,
)
from match_service.domain.model import Phase
from match_service.domain.queue import MATCHED, RESERVED, SEARCHING

log = structlog.get_logger(__name__)

MS = 1000


@dataclass(frozen=True, slots=True)
class QueueLimits:
    """Лимиты очереди (``GV_MATCH__*``)."""

    enter_per_min: int
    tick_per_min: int
    pending_room_ms: int
    tick_s: int = 5
    window_s: int = 60
    presence_stale_ms: int = 0
    """Молчащий в комнате дольше игрок комнату не удерживает (``presence_timeout_s``)."""


class QueueService:
    """Вход в очередь подбора, heartbeat, выход и размер очередей."""

    def __init__(
        self,
        *,
        queue: QueueStore,
        ladders: LadderProjection,
        templates: RankedTemplates,
        player_rooms: PlayerRooms,
        store: RoomStore,
        bus: CommandBus,
        presence: PresenceStore,
        limiter: RateLimiter,
        creates: IdempotentCreates,
        clock: Clock,
        limits: QueueLimits,
        sanctions: SanctionGate | None = None,
    ) -> None:
        self._sanctions = sanctions
        self._queue = queue
        self._ladders = ladders
        self._templates = templates
        self._player_rooms = player_rooms
        self._store = store
        self._bus = bus
        self._presence = presence
        self._limiter = limiter
        self._creates = creates
        self._clock = clock
        self._limits = limits

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * MS)

    # ------------------------------------------------------------------ enter
    async def enter(self, actor: Actor, ladder_key: str, fmt: str, key: str) -> Json:
        """Встать в очередь ``(ladder, format)``; повтор — то же состояние.

        Raises:
            MatchForbiddenError: ``match.ranked_forbidden`` (санкция ``RANKED``).
            MatchNotFoundError: ``match.ladder_not_found``.
            MatchInvalidError: ``match.queue_format_unavailable``.
            MatchConflictError: ``match.active_room_conflict`` (комната или другая очередь).
            ConflictError: ``idempotency.key_reused``.
            MatchRateLimitedError: лимит входов.
            MatchUnavailableError: game недоступен при чтении рейтингового шаблона.
        """
        actor = await effective_actor(self._sanctions, actor, strict=True)  # вход: fail-closed
        if actor.online_banned:
            raise online_banned()
        if actor.ranked_banned:
            raise MatchForbiddenError(
                "рейтинговые игры вам недоступны", code=errors.RANKED_FORBIDDEN
            )
        await self._limiter.hit(
            "queue_enter",
            str(actor.user_id),
            limit=self._limits.enter_per_min,
            window_s=self._limits.window_s,
        )
        fingerprint = request_fingerprint(
            "queue.enter", None, {"ladder": ladder_key, "format": fmt}
        )
        idem = f"queue:{key}"
        saved = await self._creates.get(actor.user_id, idem)
        if saved is not None:
            if saved[0] != fingerprint:
                raise ConflictError(
                    "Idempotency-Key уже использован с другим запросом",
                    code="idempotency.key_reused",
                )
            record = await self._queue.get(actor.user_id)
            if record is None:
                raise MatchNotFoundError("вы не в очереди", code=errors.NOT_IN_QUEUE)
            return await self._ticket(record)
        ladder = await self._ladders.ladder(ladder_key)
        if ladder is None:
            raise MatchNotFoundError("такого ladder-а нет", code=errors.LADDER_NOT_FOUND)
        if fmt not in ladder.formats or await self._templates.resolve(ladder, fmt) is None:
            raise MatchInvalidError(
                "этот формат очереди сейчас недоступен", code=errors.QUEUE_FORMAT_UNAVAILABLE
            )
        existing = await self._existing(actor, ladder_key, fmt)
        if existing is None:
            await self._queue.take_failure(actor.user_id)  # новый поиск - старая ошибка не нужна
            rating = await self._ladders.rating(actor.user_id, ladder_key)
            existing, _ = await self._queue.enter(
                actor.user_id,
                ladder_key,
                fmt,
                rating if rating is not None else ladder.start_rating,
                self._now_ms(),
            )
        await self._creates.put(actor.user_id, idem, fingerprint, f"{ladder_key}:{fmt}")
        return await self._ticket(existing, ladder)

    async def _existing(self, actor: Actor, ladder: str, fmt: str) -> QueueRecord | None:
        """Текущая запись игрока (идемпотентный повтор) или конфликт; устаревшая — убирается."""
        record = await self._queue.get(actor.user_id)
        if record is not None:
            if record.state == MATCHED and not await self._room_alive(record):
                await self._queue.remove(actor.user_id)
            else:
                if (record.ladder, record.format) != (ladder, fmt):
                    raise MatchConflictError(
                        "вы уже стоите в другой очереди",
                        code=errors.ACTIVE_ROOM_CONFLICT,
                        details={"queue": {"ladder": record.ladder, "format": record.format}},
                    )
                return record
        active = await self._active_room(actor)
        if active is not None:
            raise MatchConflictError(
                "у вас уже есть активная комната",
                code=errors.ACTIVE_ROOM_CONFLICT,
                details={"code": active.code, "phase": active.phase},
            )
        return None

    async def _room_alive(self, record: QueueRecord) -> bool:
        if record.room_code is None:
            return False
        room = await self._store.load(record.room_code)
        return room is not None and not room.is_closed

    async def _active_room(self, actor: Actor) -> ActiveRoom | None:
        """Активная комната игрока (правило D10, одно с ``claim_room``)."""
        return await active_room_of(
            self._player_rooms,
            self._store,
            actor.user_id,
            now_ms=self._now_ms(),
            pending_ms=self._limits.pending_room_ms,
            presence=self._presence,
            stale_ms=self._limits.presence_stale_ms,
        )

    # ------------------------------------------------------------------ tick
    async def tick(self, actor: Actor) -> Json:
        """Heartbeat и состояние; при ``matched`` ещё и присутствие в комнате (отсчёт идёт).

        Raises:
            MatchNotFoundError: ``match.not_in_queue``.
        """
        await self._limiter.hit(
            "queue_tick",
            str(actor.user_id),
            limit=self._limits.tick_per_min,
            window_s=self._limits.window_s,
        )
        actor = await effective_actor(self._sanctions, actor)
        if actor.online_banned:
            await self._queue.remove(actor.user_id)
            raise online_banned()
        if actor.ranked_banned:
            # Санкцию могли выдать, пока игрок стоял в очереди: запись снимается.
            await self._queue.remove(actor.user_id)
            raise MatchForbiddenError(
                "рейтинговые игры вам недоступны", code=errors.RANKED_FORBIDDEN
            )
        record = await self._queue.get(actor.user_id)
        if record is None or not await self._queue.touch(actor.user_id):
            if await self._queue.take_failure(actor.user_id) == errors.START_UNAVAILABLE:
                # Матчер сдался после серии сорванных стартов: говорим об этом один раз.
                raise MatchUnavailableError(
                    "не удалось запустить матч, попробуйте позже", code=errors.START_UNAVAILABLE
                )
            raise MatchNotFoundError("вы не в очереди", code=errors.NOT_IN_QUEUE)
        if record.state == MATCHED and record.room_code is not None:
            await self._presence.touch(record.room_code, actor.user_id, self._now_ms())
        return await self._ticket(record)

    # ------------------------------------------------------------------ leave
    async def leave(self, actor: Actor, key: str) -> None:
        """Выйти из очереди (идемпотентно). Подбор уже состоялся, отсчёт идёт — выход из
        комнаты без штрафа (срыв отсчёта); матч идёт — очередь тут бессильна (сдача — в комнате).
        """
        await self._limiter.hit(
            "queue_enter",
            str(actor.user_id),
            limit=self._limits.enter_per_min,
            window_s=self._limits.window_s,
        )
        record = await self._queue.get(actor.user_id)
        if record is None:
            return
        if record.state == RESERVED:
            # Комната создаётся: запись не трогаем (иначе пара развалится), клиент повторит.
            raise MatchConflictError(
                "подбор завершается, повторите через секунду",
                code=errors.QUEUE_MATCHING,
                details={"retry_after": 1},
            )
        if record.state == MATCHED and record.room_code is not None:
            room = await self._store.load(record.room_code)
            counting = (
                room is not None
                and not room.is_closed
                and room.phase is Phase.COUNTDOWN
                and room.match is not None
                and room.match.started_at_ms is None
            )
            if not counting:
                return
            await self._bus.send(
                record.room_code,
                RoomCommand(
                    op_id=f"queue-leave-{key}"[:128], kind="leave", user_id=actor.user_id, data={}
                ),
            )
        await self._queue.remove(actor.user_id)

    # ------------------------------------------------------------------ pool
    async def pool(self) -> Json:
        """Сколько игроков ищут матч по каждой очереди (без списка игроков)."""
        items: list[Json] = []
        for ladder in await self._ladders.all():
            counts = [await self._queue.count(ladder.ladder, fmt) for fmt in ladder.formats]
            items.extend(
                {"ladder": ladder.ladder, "format": fmt, "searching": count}
                for fmt, count in zip(ladder.formats, counts, strict=True)
            )
        return {"items": items}

    # ------------------------------------------------------------------ вид
    async def _ticket(self, record: QueueRecord, ladder: LadderInfo | None = None) -> Json:
        config = ladder or await self._ladders.ladder(record.ladder)
        wait_s = max(0, self._now_ms() - record.entered_at_ms) // MS
        return {
            "ladder": record.ladder,
            "format": record.format,
            # «reserved» — комната ещё создаётся: клиенту это тот же поиск.
            "state": MATCHED if record.state == MATCHED else SEARCHING,
            "entered_at": iso(record.entered_at_ms),
            "tick_s": self._limits.tick_s,
            "wait_s": wait_s,
            "window": config.matchmaking.window_at(wait_s) if config is not None else 0,
            "slow": config.matchmaking.is_slow(wait_s) if config is not None else False,
            "room_code": record.room_code if record.state == MATCHED else None,
        }
