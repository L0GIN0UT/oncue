"""Команды комнат на стороне api (R3, R9, D6, D12–D14).

Процесс ``api`` не владеет комнатами: он проверяет то, что можно проверить без владельца
(лимиты, санкции из claim, UGC чата, существование комнаты), кладёт команду в поток комнаты
и ждёт ответ владельца ≤ ``GV_MATCH__REPLY_TIMEOUT_MS`` — иначе 202 ``{accepted, version}``.
Права на действия в комнате (участник, хост) проверяет владелец (``RoomOwner``).

Создание комнаты — здесь: шаблон (версия фиксируется), переопределения хоста, одна активная
комната на игрока, уникальный код (``SET NX``), ``Idempotency-Key`` (комната живёт в Redis).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError
from gv_core.random import Random
from gv_db.idempotency import request_fingerprint
from gv_game_kernel import ConfigError
from gv_text.ugc import (
    UGC_DENIED,
    UGC_EMAIL,
    UGC_EMPTY,
    UGC_LINK,
    UGC_MENTION,
    UGC_TOO_LONG,
    UgcPolicy,
    UgcViolation,
)
from match_service.application.owner import new_code
from match_service.application.ports import (
    CommandBus,
    IdempotentCreates,
    Json,
    ModeSource,
    PlayerRooms,
    RateLimiter,
    RoomCommand,
    RoomStore,
    Users,
)
from match_service.application.rules import RulesFactory
from match_service.application.views import room_view
from match_service.domain import errors
from match_service.domain.errors import (
    MatchConflictError,
    MatchForbiddenError,
    MatchInvalidError,
    MatchNotFoundError,
    MatchUnavailableError,
    error_for_status,
)
from match_service.domain.model import Presence
from match_service.domain.room import Room, SettingsChange, apply_settings, default_settings

log = structlog.get_logger(__name__)

CHAT_MAX_LENGTH = 200
"""Длина сообщения чата после нормализации (контракт ``ChatRequest``)."""
_CODE_ATTEMPTS = 8
_UGC_REASONS = {
    UGC_DENIED: "denylist",
    UGC_LINK: "link",
    UGC_MENTION: "link",
    UGC_EMAIL: "link",
    UGC_TOO_LONG: "too_long",
    UGC_EMPTY: "empty",
}


@dataclass(frozen=True, slots=True)
class Actor:
    """Пользователь запроса (гость сюда не доходит: ``require_user``).

    Attributes:
        chat_muted: Санкция ``CHAT_MUTE`` из claim.
        chat_slow: Санкция ``CHAT_SLOW`` из claim.
        ip: IP клиента (лимит перебора кодов).
    """

    user_id: UUID
    chat_muted: bool = False
    chat_slow: bool = False
    ip: str | None = None


@dataclass(frozen=True, slots=True)
class RoomLimits:
    """Лимиты и таймауты api (``GV_MATCH__*``)."""

    reply_timeout_ms: int
    create_per_hour: int
    join_by_code_per_ip_per_min: int
    commands_per_s: int
    chat_per_min: int
    chat_slow_interval_s: int


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Итог команды: 200 — ответ владельца, 202 — команда в потоке, ответа не дождались."""

    status: int
    body: Json


class RoomService:
    """Создание комнат и отправка команд владельцу."""

    def __init__(
        self,
        *,
        store: RoomStore,
        bus: CommandBus,
        player_rooms: PlayerRooms,
        modes: ModeSource,
        factory: RulesFactory,
        users: Users,
        limiter: RateLimiter,
        creates: IdempotentCreates,
        ugc: UgcPolicy,
        clock: Clock,
        random: Random,
        limits: RoomLimits,
    ) -> None:
        self._store = store
        self._bus = bus
        self._player_rooms = player_rooms
        self._modes = modes
        self._factory = factory
        self._users = users
        self._limiter = limiter
        self._creates = creates
        self._ugc = ugc
        self._clock = clock
        self._random = random
        self._limits = limits

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    # ------------------------------------------------------------------ создание
    async def create(
        self,
        actor: Actor,
        *,
        mode_key: str,
        change: SettingsChange,
        key: str,
        request: Json,
    ) -> Json:
        """Создать комнату по онлайн-шаблону; создатель — хост в слоте 0.

        Raises:
            MatchNotFoundError: ``match.mode_not_found``.
            MatchInvalidError: ``match.mode_not_online``, ``match.setting_not_allowed``,
                ``match.collection_required``, ``match.too_many_collections``.
            MatchForbiddenError: ``match.ranked_forbidden``.
            MatchConflictError: ``match.active_room_conflict``.
            ConflictError: ``idempotency.key_reused``.
            MatchRateLimitedError: лимит создания.
        """
        fingerprint = request_fingerprint("room.create", None, request)
        saved = await self._creates.get(actor.user_id, key)
        if saved is not None:
            if saved[0] != fingerprint:
                raise ConflictError(
                    "Idempotency-Key уже использован с другим запросом",
                    code="idempotency.key_reused",
                )
            existing = await self._store.load(saved[1])
            if existing is not None:
                return room_view(existing, self._now_ms())
        await self._limiter.hit(
            "create", str(actor.user_id), limit=self._limits.create_per_hour, window_s=3600
        )
        version = await self._modes.current(mode_key)
        body = await self._modes.body(mode_key, version) if version is not None else None
        if version is None or body is None:
            raise MatchNotFoundError("нет такого онлайн-шаблона", code=errors.MODE_NOT_FOUND)
        try:
            tpl = self._factory.template(mode_key, version, body)
        except ConfigError as exc:
            log.warning("match.template_invalid", mode_key=mode_key, details=exc.details)
            raise MatchNotFoundError(
                "шаблон недоступен для онлайна", code=errors.MODE_NOT_FOUND
            ) from exc
        if tpl.mode.ranked:
            raise MatchForbiddenError(
                "рейтинговые комнаты — только из очереди", code=errors.RANKED_FORBIDDEN
            )
        settings = apply_settings(tpl.template, default_settings(tpl.template), change)
        await self._require_free(actor.user_id)
        nickname = await self._users.nickname(actor.user_id) or ""
        for _ in range(_CODE_ATTEMPTS):
            room = Room.create(
                code=new_code(self._random),
                mode=tpl.mode,
                template=tpl.template,
                settings=settings,
                host_id=actor.user_id,
                nickname=nickname,
                now_ms=self._now_ms(),
            )
            if await self._store.create(room):
                await self._player_rooms.assign(actor.user_id, room.code)
                await self._creates.put(actor.user_id, key, fingerprint, room.code)
                log.info("match.room_created", room=room.code, mode_key=mode_key)
                return room_view(room, self._now_ms())
        raise MatchUnavailableError("не удалось выдать код комнаты")

    async def _require_free(self, user_id: UUID) -> None:
        """У игрока нет другой активной комнаты (D10)."""
        current = await self._player_rooms.get(user_id)
        if current is None:
            return
        room = await self._store.load(current)
        seat = room.seat_of(user_id) if room is not None else None
        active = room is not None and not room.is_closed
        if active and seat is not None and seat.presence is not Presence.LEFT:
            raise MatchConflictError(
                "у вас уже есть активная комната",
                code=errors.ACTIVE_ROOM_CONFLICT,
                details={"code": current},
            )

    # ------------------------------------------------------------------ команды
    async def command(
        self, actor: Actor, code: str, kind: str, op_id: str, data: Json | None = None
    ) -> CommandOutcome:
        """Отправить команду владельцу комнаты и дождаться ответа.

        Raises:
            AppError: ответ владельца с ошибкой (``match.*``) или 404/429/503 здесь.
        """
        room = await self._require_room(code)
        command = RoomCommand(op_id=op_id, kind=kind, user_id=actor.user_id, data=data or {})
        saved = room.op_reply(command.reply_key)
        if saved is None:
            await self._limiter.hit(
                "cmd", str(actor.user_id), limit=self._limits.commands_per_s, window_s=1
            )
            await self._bus.send(code, command)
            saved = await self._bus.wait_reply(code, command, self._limits.reply_timeout_ms)
        if saved is None:
            latest = await self._store.load(code)
            version = latest.version if latest is not None else room.version
            return CommandOutcome(202, {"accepted": True, "version": version})
        return CommandOutcome(200, unwrap(saved))

    async def _require_room(self, code: str) -> Room:
        room = await self._store.load(code)
        if room is None or room.is_closed:
            raise MatchNotFoundError("комнаты нет или она закрыта", code=errors.ROOM_NOT_FOUND)
        return room

    async def join(self, actor: Actor, code: str, op_id: str) -> CommandOutcome:
        """Войти по коду (лимит перебора кодов — по IP, fail-closed)."""
        await self._limiter.hit(
            "join_ip",
            actor.ip or "unknown",
            limit=self._limits.join_by_code_per_ip_per_min,
            window_s=60,
            fail_closed=True,
        )
        nickname = await self._users.nickname(actor.user_id) or ""
        return await self.command(actor, code, "join", op_id, {"nickname": nickname})

    async def emote(
        self, actor: Actor, code: str, op_id: str, kind: str, key: str
    ) -> CommandOutcome:
        """Эмоция или фраза (``CHAT_MUTE`` — 403)."""
        if actor.chat_muted:
            raise MatchForbiddenError("чат ограничен", code=errors.CHAT_MUTED)
        return await self.command(actor, code, "emote", op_id, {"kind": kind, "key": key})

    async def chat(self, actor: Actor, code: str, op_id: str, text: str) -> CommandOutcome:
        """Свободный чат лобби: санкции, лимиты, ``gv_text.ugc`` + denylist (ADR 0030).

        Raises:
            MatchForbiddenError: ``match.chat_muted``.
            MatchRateLimitedError: ``CHAT_SLOW`` или ``GV_MATCH__CHAT_PER_PLAYER_PER_MIN``.
            MatchInvalidError: ``match.chat_rejected`` (``details.reason``).
        """
        if actor.chat_muted:
            raise MatchForbiddenError("чат ограничен", code=errors.CHAT_MUTED)
        try:
            cleaned = self._ugc.check(text, max_len=CHAT_MAX_LENGTH, forbid_links=True)
        except UgcViolation as exc:
            raise MatchInvalidError(
                "сообщение отклонено",
                code=errors.CHAT_REJECTED,
                details={"reason": _UGC_REASONS.get(exc.code, "denylist")},
            ) from exc
        user = str(actor.user_id)
        if actor.chat_slow:
            interval = self._limits.chat_slow_interval_s
            await self._limiter.hit("chat_slow", user, limit=1, window_s=interval)
        await self._limiter.hit("chat", user, limit=self._limits.chat_per_min, window_s=60)
        return await self.command(actor, code, "chat", op_id, {"text": cleaned})

    async def abort(self, admin_id: UUID, code: str, op_id: str, reason: str) -> CommandOutcome:
        """Принудительное завершение комнаты (админ, ``match.manage``).

        Raises:
            MatchNotFoundError: ``match.room_not_found``.
            MatchConflictError: ``match.room_closed``.
        """
        room = await self._store.load(code)
        if room is None:
            raise MatchNotFoundError("комнаты нет", code=errors.ROOM_NOT_FOUND)
        if room.is_closed:
            raise MatchConflictError("комната уже закрыта", code=errors.ROOM_CLOSED)
        log.info("match.admin_abort", room=code, admin_id=str(admin_id), reason_len=len(reason))
        outcome = await self.command(Actor(admin_id), code, "abort", op_id, {})
        return CommandOutcome(202, {"accepted": True, "version": outcome.body["version"]})


def unwrap(reply: Json) -> Json:
    """Тело успешного ответа владельца или его ошибка (``AppError``)."""
    status = int(reply.get("status", 503))
    if status == 200:  # noqa: PLR2004 — HTTP OK
        body: Json = reply.get("body") or {}
        return body
    raise error_for_status(
        status,
        str(reply.get("code") or errors.ROOMS_UNAVAILABLE),
        str(reply.get("message") or ""),
        reply.get("details") or None,
    )
