"""Команды комнат на стороне api (R3, R9, D6, D12–D14).

Процесс ``api`` не владеет комнатами: он проверяет то, что можно проверить без владельца
(лимиты, санкции из claim, UGC чата, существование комнаты), кладёт команду в поток комнаты
и ждёт ответ владельца ≤ ``GV_MATCH__REPLY_TIMEOUT_MS`` — иначе 202 ``{accepted, version}``.
Права на действия в комнате (участник, хост) проверяет владелец (``RoomOwner``).

Создание комнаты — здесь: шаблон (версия фиксируется), переопределения хоста, одна активная
комната на игрока, уникальный код (``SET NX``), ``Idempotency-Key`` (комната живёт в Redis).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from uuid import UUID

import structlog

from gv_auth import Sanction
from gv_core.clock import Clock
from gv_core.errors import ConflictError
from gv_core.ids import uuid7
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
from match_service.application.owner import claim_room, new_code, replay
from match_service.application.ports import (
    AdminAction,
    AdminAudit,
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
from match_service.application.rules import ModeTemplate, RulesFactory
from match_service.application.sanctions import SanctionGate, online_banned
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
from match_service.domain.model import RoomSettings
from match_service.domain.room import Room, SettingsChange, apply_settings, default_settings

log = structlog.get_logger(__name__)

CHAT_MAX_LENGTH = 200
"""Длина сообщения чата после нормализации (контракт ``ChatRequest``)."""
ONLINE_ENTRY_KINDS = frozenset({"join", "rematch"})
"""Команды входа в (новую) комнату: ``ONLINE`` закрывает их, недоступная проекция - 503."""
ONLINE_EXEMPT_KINDS = frozenset({"leave", "abort"})
"""Остальные команды при ``ONLINE`` закрыты; выход всегда разрешён, админский abort - не игрок."""
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
        ranked_banned: Санкция ``RANKED`` из claim (нет рейтинговых игр).
        ip: IP клиента (лимит перебора кодов).
    """

    user_id: UUID
    chat_muted: bool = False
    chat_slow: bool = False
    ranked_banned: bool = False
    ip: str | None = None
    online_banned: bool = False
    """Санкция ``ONLINE`` (ADR 0052): комнаты, лобби, очереди, реванш и приглашения закрыты."""
    claim_sanctions: int = 0
    """Маска claim токена: основа эффективной маски, если проекции игрока нет."""

    def with_sanctions(self, mask: int) -> Actor:
        """Копия с флагами, пересчитанными по эффективной маске."""
        return replace(
            self,
            chat_muted=bool(mask & Sanction.CHAT_MUTE),
            chat_slow=bool(mask & Sanction.CHAT_SLOW),
            ranked_banned=bool(mask & Sanction.RANKED),
            online_banned=bool(mask & Sanction.ONLINE),
        )


async def effective_actor(
    gate: SanctionGate | None, actor: Actor, *, strict: bool = False
) -> Actor:
    """Субъект с санкциями по проекции (иначе по claim): проекция авторитетна для снятия.

    ``strict`` - вход в онлайн: недоступная проекция даёт 503, а не деградацию до claim.
    """
    if gate is None:
        return actor
    return actor.with_sanctions(
        await gate.mask(actor.user_id, actor.claim_sanctions, strict=strict)
    )


async def require_online(gate: SanctionGate | None, actor: Actor, *, strict: bool = True) -> None:
    """Пропустить игрока без санкции ONLINE.

    Raises:
        MatchForbiddenError: ``match.online_banned``.
        MatchUnavailableError: ``match.sanctions_unavailable`` (``strict``, проекция недоступна).
    """
    if gate is not None:
        await gate.require_online(actor.user_id, actor.claim_sanctions, strict=strict)
    elif actor.online_banned:
        raise online_banned()


@dataclass(frozen=True, slots=True)
class RoomLimits:
    """Лимиты и таймауты api (``GV_MATCH__*``)."""

    reply_timeout_ms: int
    create_per_hour: int
    join_by_code_per_ip_per_min: int
    commands_per_s: int
    chat_per_min: int
    chat_slow_interval_s: int
    join_by_code_per_user_per_min: int = 20
    emote_per_min: int = 30
    create_lock_ms: int = 5000
    pending_room_ms: int = 10_000


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
        audit: AdminAudit | None = None,
        sanctions: SanctionGate | None = None,
    ) -> None:
        self._sanctions = sanctions
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
        self._audit = audit

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
        # Санкция ONLINE - до всего остального; недоступная проекция - 503 (fail-closed).
        await require_online(self._sanctions, actor)
        fingerprint = request_fingerprint("room.create", None, request)
        replayed = await self._replay_create(actor, key, fingerprint)
        if replayed is not None:
            return replayed
        # Замок до лимита и проверок (аудит Н9): параллельные создания одним игроком
        # (проверка «одна активная комната» читает указатель, а комнаты по нему ещё нет).
        # Токен — отпечаток ключа и случайный хвост: снимаем только свой замок, а повтор
        # с тем же ключом во время создания узнаём по отпечатку.
        owner = hashlib.sha256(key.encode()).hexdigest()[:16]
        token = f"{owner}:{uuid7().hex}"
        if not await self._player_rooms.lock_create(
            actor.user_id, token, self._limits.create_lock_ms
        ):
            holder = await self._player_rooms.create_holder(actor.user_id)
            if holder is not None and holder.partition(":")[0] == owner:
                raise ConflictError(
                    "запрос с этим Idempotency-Key ещё выполняется",
                    code="idempotency.in_progress",
                )
            raise MatchConflictError("комната уже создаётся", code=errors.ACTIVE_ROOM_CONFLICT)
        try:
            replayed = await self._replay_create(actor, key, fingerprint)
            if replayed is not None:
                return replayed
            await self._limiter.hit(
                "create", str(actor.user_id), limit=self._limits.create_per_hour, window_s=3600
            )
            tpl = await self._template(mode_key)
            settings = apply_settings(tpl.template, default_settings(tpl.template), change)
            nickname = await self._users.nickname(actor.user_id) or ""
            return await self._create_room(
                actor,
                tpl=tpl,
                settings=settings,
                nickname=nickname,
                key=key,
                fingerprint=fingerprint,
            )
        finally:
            await self._player_rooms.unlock_create(actor.user_id, token)

    async def _replay_create(self, actor: Actor, key: str, fingerprint: str) -> Json | None:
        """Повтор ``Idempotency-Key``: та же комната или 409 при другом теле."""
        saved = await self._creates.get(actor.user_id, key)
        if saved is None:
            return None
        if saved[0] != fingerprint:
            raise ConflictError(
                "Idempotency-Key уже использован с другим запросом",
                code="idempotency.key_reused",
            )
        existing = await self._store.load(saved[1])
        return room_view(existing, self._now_ms()) if existing is not None else None

    async def _template(self, mode_key: str) -> ModeTemplate:
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
        return tpl

    async def _create_room(
        self,
        actor: Actor,
        *,
        tpl: ModeTemplate,
        settings: RoomSettings,
        nickname: str,
        key: str,
        fingerprint: str,
    ) -> Json:
        mode_key = tpl.mode.key
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
            # Сначала занять «одну активную комнату» (SET NX), потом создать комнату:
            # два параллельных создания одним игроком не дадут двух комнат.
            await self._claim(actor.user_id, room.code)
            try:
                created = await self._store.create(room)
            except BaseException:
                await self._player_rooms.release(actor.user_id, room.code)
                raise
            if created:
                await self._creates.put(actor.user_id, key, fingerprint, room.code)
                log.info("match.room_created", room=room.code, mode_key=mode_key)
                return room_view(room, self._now_ms())
            await self._player_rooms.release(actor.user_id, room.code)
        raise MatchUnavailableError("не удалось выдать код комнаты")

    async def _claim(self, user_id: UUID, code: str) -> None:
        """Занять ``player:{id}:room`` под новую комнату (D10, ``claim_room``)."""
        await claim_room(
            self._player_rooms,
            self._store,
            user_id,
            code,
            now_ms=self._now_ms(),
            pending_ms=self._limits.pending_room_ms,
        )

    # ------------------------------------------------------------------ команды
    async def command(
        self, actor: Actor, code: str, kind: str, op_id: str, data: Json | None = None
    ) -> CommandOutcome:
        """Отправить команду владельцу комнаты и дождаться ответа.

        Raises:
            AppError: ответ владельца с ошибкой (``match.*``) или 404/429/503 здесь.
            MatchForbiddenError: ``match.online_banned`` (все команды, кроме ``leave``/``abort``).
            MatchUnavailableError: ``match.sanctions_unavailable`` для ``join``/``rematch``;
                для остальных команд игрока в комнате проекция деградирует до claim.
        """
        if kind not in ONLINE_EXEMPT_KINDS:
            await require_online(self._sanctions, actor, strict=kind in ONLINE_ENTRY_KINDS)
        room = await self._require_room(code)
        command = RoomCommand(op_id=op_id, kind=kind, user_id=actor.user_id, data=data or {})
        stored = room.op_reply(actor.user_id, op_id)
        saved = replay(room, command, stored, self._now_ms()) if stored is not None else None
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
        await code_lookup_limits(
            self._limiter,
            actor,
            per_ip=self._limits.join_by_code_per_ip_per_min,
            per_user=self._limits.join_by_code_per_user_per_min,
        )
        nickname = await self._users.nickname(actor.user_id) or ""
        outcome = await self.command(actor, code, "join", op_id, {"nickname": nickname})
        await self._evict_if_banned_meanwhile(actor, code)
        return outcome

    async def _evict_if_banned_meanwhile(self, actor: Actor, code: str) -> None:
        """Повторная проверка ONLINE после входа: закрывает гонку «join - бан».

        Событие бана могло отработать, пока ``player:{id}:room`` ещё не стоял (evict
        владельцу некуда слать). Если бан появился между проверкой и входом, игрока выбиваем
        системной командой и отвечаем 403. Остаточное окно (миллисекунды между коммитом
        события и этой проверкой) безопасно: любая команда забаненного уже отвечает 403.
        """
        if self._sanctions is None:
            return
        try:
            await self._sanctions.require_online(actor.user_id, actor.claim_sanctions, strict=False)
        except MatchForbiddenError:
            evict = RoomCommand(
                op_id=f"sys-evict-{uuid7().hex}", kind="evict", user_id=actor.user_id, data={}
            )
            await self._bus.send(code, evict)
            raise

    async def emote(
        self, actor: Actor, code: str, op_id: str, kind: str, key: str
    ) -> CommandOutcome:
        """Эмоция или фраза: ``CHAT_MUTE`` — 403, ``CHAT_SLOW`` и свой лимит — 429."""
        actor = await effective_actor(self._sanctions, actor)
        if actor.chat_muted:
            raise MatchForbiddenError("чат ограничен", code=errors.CHAT_MUTED)
        user = str(actor.user_id)
        if actor.chat_slow:
            interval = self._limits.chat_slow_interval_s
            await self._limiter.hit("emote_slow", user, limit=1, window_s=interval)
        await self._limiter.hit("emote", user, limit=self._limits.emote_per_min, window_s=60)
        return await self.command(actor, code, "emote", op_id, {"kind": kind, "key": key})

    async def chat(self, actor: Actor, code: str, op_id: str, text: str) -> CommandOutcome:
        """Свободный чат лобби: санкции, лимиты, ``gv_text.ugc`` + denylist (ADR 0030).

        Raises:
            MatchForbiddenError: ``match.chat_muted``.
            MatchRateLimitedError: ``CHAT_SLOW`` или ``GV_MATCH__CHAT_PER_PLAYER_PER_MIN``.
            MatchInvalidError: ``match.chat_rejected`` (``details.reason``).
        """
        actor = await effective_actor(self._sanctions, actor)
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

        Текст причины — только в аудит (``admin_actions``); в ``MatchFinished.abort_reason``
        уходит машинный код ``admin``.

        Raises:
            MatchNotFoundError: ``match.room_not_found``.
            MatchConflictError: ``match.room_closed``.
            ConflictError: ``idempotency.key_reused`` — ключ уже потрачен на другое действие.
            MatchUnavailableError: аудит недоступен (без БД abort не выполняется).
        """
        room = await self._store.load(code)
        if room is None:
            raise MatchNotFoundError("комнаты нет", code=errors.ROOM_NOT_FOUND)
        if room.is_closed:
            raise MatchConflictError("комната уже закрыта", code=errors.ROOM_CLOSED)
        if self._audit is None:
            # Без аудита админское действие не выполняется (fail-closed, аудит Н4).
            raise MatchUnavailableError("аудит недоступен: база данных не настроена")
        requested = AdminAction(
            actor_id=admin_id,
            action="room.abort",
            room_code=code,
            match_id=room.match.match_id if room.match is not None else None,
            reason=reason,
            idempotency_key=op_id,
            at=self._clock.now(),
        )
        stored = await self._audit.record(requested)
        if (stored.action, stored.room_code, stored.reason) != (
            requested.action,
            requested.room_code,
            requested.reason,
        ):
            # Ключ уже использован для другого действия: иначе abort прошёл бы без аудита.
            raise ConflictError(
                "Idempotency-Key уже использован с другим запросом",
                code="idempotency.key_reused",
            )
        log.info("match.admin_abort", room=code, admin_id=str(admin_id))
        outcome = await self.command(Actor(admin_id), code, "abort", op_id, {})
        return CommandOutcome(202, {"accepted": True, "version": outcome.body["version"]})


async def code_lookup_limits(
    limiter: RateLimiter, actor: Actor, *, per_ip: int, per_user: int
) -> None:
    """Перебор кодов комнат (D12): лимит по IP и по пользователю, оба fail-closed."""
    await limiter.hit("join_ip", actor.ip or "unknown", limit=per_ip, window_s=60, fail_closed=True)
    await limiter.hit(
        "join_user", str(actor.user_id), limit=per_user, window_s=60, fail_closed=True
    )


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
