"""Use-cases друзей, блокировок, кодов, приватности, присутствия и приглашений (ADR 0050).

Гонки решают транзакционные advisory-блокировки: сначала игроки (по возрастанию id), затем
пара. Поэтому два одновременных ``accept`` и две встречные заявки дают ровно одну дружбу.
Мутации идемпотентны по ``(user_id, Idempotency-Key)``: ключ и ответ пишутся в той же
транзакции (``gv_db.idempotency``). Ответы-словари - JSON, пригодный для хранения.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

import structlog

from gv_auth import Sanction
from gv_core.clock import Clock
from gv_core.errors import NotFoundError
from gv_core.ids import uuid7
from gv_core.random import Random
from gv_db.idempotency import request_fingerprint, run_idempotent
from social_service.application.config_access import current_config
from social_service.application.ports.config import ConfigProvider
from social_service.application.ports.friends import (
    AttemptLimiter,
    BlockRow,
    FriendRow,
    FriendsRepository,
    FriendsUnitOfWork,
    FriendsUowFactory,
    PendingView,
    PresenceStore,
    RelationFacts,
    RequestRow,
)
from social_service.application.ports.sanctions import SanctionReader
from social_service.domain.config import SocialConfig
from social_service.domain.friend_events import (
    FriendAdded,
    FriendRemoved,
    FriendRequested,
    RoomInviteSent,
    UserBlocked,
)
from social_service.domain.friends import (
    CannotBlockSelfError,
    InviteForbiddenError,
    InvitesRateLimitedError,
    LookupRateLimitedError,
    PlayerNotFoundError,
    PresenceRateLimitedError,
    Privacy,
    ProfileNotReadyError,
    Relation,
    RelationsRateLimitedError,
    RequestAction,
    RequestFacts,
    RequestNotPendingError,
    check_friend_capacity,
    decide_request,
    generate_friend_code,
    ordered_pair,
    relation_of,
)

LOOKUP_WINDOW_S = 3600
INVITE_WINDOW_S = 60
RELATIONS_WINDOW_S = 60
DAY = timedelta(days=1)
CODE_ATTEMPTS = 8
"""Сколько раз пробуем новый код при столкновении (алфавит 32^8 - столкновения редки)."""
log = structlog.get_logger(__name__)

STATUS_ONLINE = "online"
STATUS_IN_MATCH = "in_match"
STATUS_OFFLINE = "offline"

type Json = dict[str, object]


class RequestNotFoundError(NotFoundError):
    """Нет такой заявки у игрока (чужая - тоже 404)."""

    default_code = "social.request_not_found"

    def __init__(self) -> None:
        super().__init__("заявка не найдена")


@dataclass(frozen=True, slots=True)
class FriendStatus:
    """Друг со статусом присутствия."""

    friend: FriendRow
    status: str


@dataclass(frozen=True, slots=True)
class PendingRequests:
    """Ожидающие заявки игрока."""

    incoming: list[PendingView]
    outgoing: list[PendingView]


@dataclass(frozen=True, slots=True)
class InviteOutcome:
    """Результат приглашения."""

    invite_id: UUID
    repeated: bool


def _request_view(row: RequestRow, other: UUID, nickname: str) -> Json:
    return {
        "id": str(row.id),
        "user_id": str(other),
        "nickname": nickname,
        "created_at": row.created_at.isoformat(),
    }


class FriendsService:
    """Друзья, заявки, коды, блокировки, приватность, присутствие, приглашения."""

    def __init__(
        self,
        uow: FriendsUowFactory,
        config: ConfigProvider,
        clock: Clock,
        rng: Random,
        *,
        limiter: AttemptLimiter,
        presence: PresenceStore,
        presence_burst: int,
        relations_per_minute: int = 60,
        sanctions: SanctionReader | None = None,
    ) -> None:
        self._sanctions = sanctions
        self._uow = uow
        self._config = config
        self._clock = clock
        self._rng = rng
        self._limiter = limiter
        self._presence = presence
        self._presence_burst = presence_burst
        self._relations_per_minute = relations_per_minute

    # ------------------------------------------------------------------ заявки
    async def send_request(
        self,
        sender: UUID,
        *,
        key: str,
        nickname: str | None,
        code: str | None,
        client_ip: str | None,
        user_id: UUID | None = None,
    ) -> Json:
        """Заявка по нику, коду или id игрока; встречная заявка сразу создаёт дружбу.

        Raises:
            LookupRateLimitedError: перебор ника/кода (по игроку или IP).
            PlayerNotFoundError: неизвестный, скрывший заявки или заблокировавший игрок.
            ProfileNotReadyError: нет ника отправителя в проекции identity.
            ConflictError: ``already_friends``, ``request_pending``, ``friends_limit``.
            RateLimitedError: суточный лимит заявок.
            ForbiddenError: отправитель сам заблокировал адресата.
        """
        cfg = await current_config(self._config)
        fingerprint = request_fingerprint(
            "friend_request.create",
            None,
            {"nickname": nickname, "code": code, "user_id": user_id and str(user_id)},
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                # Лимит - только на выполняемом пути: повтор с тем же ключом бюджет не тратит.
                await self._check_lookup(cfg, sender, client_ip)
                return await self._send(
                    uow, cfg, sender, nickname=nickname, code=code, user_id=user_id
                )

            response, _ = await run_idempotent(
                uow.idempotency, sender, key, fingerprint, perform, status_code=201
            )
            await uow.commit()
        return response

    async def _send(
        self,
        uow: FriendsUnitOfWork,
        cfg: SocialConfig,
        sender: UUID,
        *,
        nickname: str | None,
        code: str | None,
        user_id: UUID | None = None,
    ) -> Json:
        repo = uow.repo
        # Ник отправителя - до поиска цели, иначе 503/404 раскрывали бы существование игрока.
        sender_nick = await repo.nickname(sender)
        if sender_nick is None:
            raise ProfileNotReadyError("профиль ещё не готов, повторите позже")
        if user_id is not None:
            # Существование - по users_proj (ниже через nickname); неизвестный id = тот же 404.
            target: UUID | None = user_id
        elif nickname is not None:
            target = await repo.user_by_nickname(nickname)
        else:
            target = await repo.user_by_code(code or "")
        target_nick = None if target is None else await repo.nickname(target)
        if target is None or target_nick is None:
            raise PlayerNotFoundError
        await repo.lock_users(sender, target)
        await repo.lock_pair(sender, target)
        now = self._clock.now()
        incoming = await repo.pending_from_to(target, sender)
        facts = RequestFacts(
            target_exists=True,
            sender_blocked_target=await repo.blocked(sender, target),
            target_blocked_sender=await repo.blocked(target, sender),
            target_policy=(await repo.privacy(target)).friend_requests,
            already_friends=await repo.are_friends(sender, target),
            outgoing_pending=await repo.pending_from_to(sender, target) is not None,
            incoming_pending=incoming is not None,
            sent_in_window=await repo.sent_since(sender, now - DAY),
            sender_friends=await repo.count_friends(sender),
            target_friends=await repo.count_friends(target),
        )
        action = decide_request(sender, target, facts, cfg.friends)
        if action is RequestAction.MERGE and incoming is not None:
            await repo.close_request(incoming.id, "accepted", now)
            await self._befriend(uow, target, sender, sender_nick, now)
            return _request_view(incoming, target, target_nick)
        row = RequestRow(
            id=uuid7(), from_user_id=sender, to_user_id=target, status="pending", created_at=now
        )
        await repo.add_request(row)
        uow.publish(
            FriendRequested(
                aggregate_id=str(row.id),
                request_id=row.id,
                from_user_id=sender,
                from_nickname=sender_nick,
                to_user_id=target,
                at=now,
            )
        )
        return _request_view(row, target, target_nick)

    async def accept(self, user: UUID, request_id: UUID, *, key: str) -> Json:
        """Принять входящую заявку (только адресат).

        Raises:
            RequestNotFoundError: нет заявки или она адресована не вам.
            RequestNotPendingError: заявка уже закрыта.
            FriendsLimitError: предел друзей у одного из игроков.
        """
        cfg = await current_config(self._config)
        fingerprint = request_fingerprint("friend_request.accept", request_id, {})
        async with self._uow() as uow:

            async def perform() -> Json:
                repo = uow.repo
                probe = await repo.get_request(request_id)
                if probe is None or probe.to_user_id != user:
                    raise RequestNotFoundError
                await repo.lock_users(probe.from_user_id, user)
                await repo.lock_pair(probe.from_user_id, user)
                row = await repo.get_request(request_id, for_update=True)
                if row is None:
                    raise RequestNotFoundError
                if row.status != "pending":
                    raise RequestNotPendingError("заявка уже обработана")
                check_friend_capacity(
                    await repo.count_friends(user),
                    await repo.count_friends(row.from_user_id),
                    cfg.friends,
                )
                nick = await repo.nickname(user)
                requester_nick = await repo.nickname(row.from_user_id)
                if nick is None or requester_nick is None:
                    raise ProfileNotReadyError("профиль ещё не готов, повторите позже")
                now = self._clock.now()
                await repo.close_request(request_id, "accepted", now)
                await self._befriend(uow, row.from_user_id, user, nick, now)
                return _request_view(row, row.from_user_id, requester_nick)

            response, _ = await run_idempotent(uow.idempotency, user, key, fingerprint, perform)
            await uow.commit()
        return response

    @staticmethod
    async def _befriend(
        uow: FriendsUnitOfWork, requester: UUID, accepter: UUID, accepter_nick: str, now: datetime
    ) -> None:
        a, b = ordered_pair(requester, accepter)
        created = await uow.repo.add_friendship(a, b, now)
        if created:
            uow.publish(
                FriendAdded(
                    aggregate_id=str(a),
                    user_a=a,
                    user_b=b,
                    requester_id=requester,
                    accepter_id=accepter,
                    accepter_nickname=accepter_nick,
                    at=now,
                )
            )

    async def decline(self, user: UUID, request_id: UUID, *, key: str) -> None:
        """Отклонить входящую заявку (повтор - no-op).

        Raises:
            RequestNotFoundError: нет заявки или она адресована не вам.
        """
        fingerprint = request_fingerprint("friend_request.decline", request_id, {})
        async with self._uow() as uow:

            async def perform() -> Json:
                row = await uow.repo.get_request(request_id, for_update=True)
                if row is None or row.to_user_id != user:
                    raise RequestNotFoundError
                if row.status == "pending":
                    await uow.repo.close_request(request_id, "declined", self._clock.now())
                return {"ok": True}

            await run_idempotent(uow.idempotency, user, key, fingerprint, perform)
            await uow.commit()

    async def cancel(self, user: UUID, request_id: UUID) -> None:
        """Отозвать исходящую заявку (повтор - no-op).

        Raises:
            RequestNotFoundError: нет заявки или она отправлена не вами.
        """
        async with self._uow() as uow:
            row = await uow.repo.get_request(request_id, for_update=True)
            if row is None or row.from_user_id != user:
                raise RequestNotFoundError
            if row.status == "pending":
                await uow.repo.close_request(request_id, "cancelled", self._clock.now())
            await uow.commit()

    async def requests(self, user: UUID) -> PendingRequests:
        """Ожидающие заявки игрока."""
        async with self._uow() as uow:
            views = await uow.repo.pending_of(user)
        return PendingRequests(
            incoming=[v for v in views if v.incoming], outgoing=[v for v in views if not v.incoming]
        )

    # ------------------------------------------------------------------ друзья
    async def friends(self, user: UUID) -> list[FriendStatus]:
        """Друзья со статусом ``online | in_match | offline``."""
        async with self._uow() as uow:
            rows = await uow.repo.friends_of(user)
            ids = [r.user_id for r in rows]
            in_match = await uow.repo.in_match(ids)
        online = await self._presence.online(ids)
        return [
            FriendStatus(
                r,
                STATUS_IN_MATCH
                if r.user_id in in_match
                else STATUS_ONLINE
                if r.user_id in online
                else STATUS_OFFLINE,
            )
            for r in rows
        ]

    async def remove_friend(self, user: UUID, other: UUID) -> None:
        """Удалить из друзей (повтор - no-op)."""
        a, b = ordered_pair(user, other)
        async with self._uow() as uow:
            await uow.repo.lock_pair(a, b)
            if await uow.repo.remove_friendship(a, b):
                uow.publish(
                    FriendRemoved(
                        aggregate_id=str(a),
                        user_a=a,
                        user_b=b,
                        removed_by=user,
                        reason="removed",
                        at=self._clock.now(),
                    )
                )
            await uow.commit()

    # ------------------------------------------------------------------ код добавления
    async def my_code(self, user: UUID) -> tuple[str, str]:
        """Код добавления (создаётся при первом запросе): ``(code, rotated_at)``."""
        async with self._uow() as uow:
            for _ in range(CODE_ATTEMPTS):
                existing = await uow.repo.code_of(user)
                if existing is not None:
                    await uow.commit()
                    return existing[0], existing[1].isoformat()
                await uow.repo.create_code(user, generate_friend_code(self._rng), self._clock.now())
        raise ProfileNotReadyError("не удалось выпустить код, повторите")

    async def regenerate_code(self, user: UUID, *, key: str) -> Json:
        """Перевыпустить код; старый перестаёт действовать."""
        fingerprint = request_fingerprint("friend_code.regenerate", None, {})
        async with self._uow() as uow:

            async def perform() -> Json:
                now = self._clock.now()
                for _ in range(CODE_ATTEMPTS):
                    code = generate_friend_code(self._rng)
                    if await uow.repo.rotate_code(user, code, now):
                        return {"code": code, "rotated_at": now.isoformat()}
                raise ProfileNotReadyError("не удалось выпустить код, повторите")

            response, _ = await run_idempotent(uow.idempotency, user, key, fingerprint, perform)
            await uow.commit()
        return response

    # ------------------------------------------------------------------ блокировки
    async def block(self, user: UUID, target: UUID) -> None:
        """Заблокировать игрока: рвёт дружбу и заявки (повтор - no-op).

        Raises:
            CannotBlockSelfError: себя.
            PlayerNotFoundError: неизвестный игрок.
        """
        if user == target:
            raise CannotBlockSelfError("себя заблокировать нельзя")
        a, b = ordered_pair(user, target)
        async with self._uow() as uow:
            repo = uow.repo
            if await repo.nickname(target) is None:
                raise PlayerNotFoundError
            await repo.lock_pair(a, b)
            now = self._clock.now()
            if await repo.add_block(user, target, now):
                await repo.close_pending_between(a, b, now)
                if await repo.remove_friendship(a, b):
                    uow.publish(
                        FriendRemoved(
                            aggregate_id=str(a),
                            user_a=a,
                            user_b=b,
                            removed_by=user,
                            reason="blocked",
                            at=now,
                        )
                    )
                uow.publish(
                    UserBlocked(aggregate_id=str(user), blocker_id=user, blocked_id=target, at=now)
                )
            await uow.commit()

    async def unblock(self, user: UUID, target: UUID) -> None:
        """Снять блокировку (дружба не восстанавливается; повтор - no-op)."""
        async with self._uow() as uow:
            await uow.repo.remove_block(user, target)
            await uow.commit()

    async def blocks(self, user: UUID) -> list[BlockRow]:
        """Блокировки владельца."""
        async with self._uow() as uow:
            return await uow.repo.blocks_of(user)

    # ------------------------------------------------------------------ приватность
    async def privacy(self, user: UUID) -> Privacy:
        """Настройки приватности."""
        async with self._uow() as uow:
            return await uow.repo.privacy(user)

    async def set_privacy(self, user: UUID, value: Privacy) -> Privacy:
        """Заменить настройки целиком."""
        async with self._uow() as uow:
            await uow.repo.save_privacy(user, value, self._clock.now())
            await uow.commit()
        return value

    async def relation(self, viewer: UUID, target: UUID) -> Relation:
        """Что ``viewer`` видит у ``target`` (для скрытого/заблокировавшего/неизвестного - одно)."""
        async with self._uow() as uow:
            return await self._relation(uow.repo, viewer, target)

    async def relations(self, viewer: UUID, targets: list[UUID]) -> list[tuple[UUID, Relation]]:
        """``relation`` для набора игроков (порядок запроса, дубли схлопнуты).

        Факты читаются пакетом (``IN``): число запросов к БД не зависит от размера пачки.

        Raises:
            RelationsRateLimitedError: больше ``relations_per_minute`` вызовов в минуту.
            LimiterUnavailableError: лимитер недоступен (fail-closed).
        """
        window = await self._limiter.hit(f"relations:{viewer}", RELATIONS_WINDOW_S)
        if window.count > self._relations_per_minute:
            raise RelationsRateLimitedError(
                "слишком частые запросы отношений", details={"retry_after": window.resets_in_s}
            )
        unique = list(dict.fromkeys(targets))
        async with self._uow() as uow:
            facts = await uow.repo.relation_facts(viewer, unique)
        return [(target, self._relation_from(viewer, target, facts[target])) for target in unique]

    @staticmethod
    async def _relation(repo: FriendsRepository, viewer: UUID, target: UUID) -> Relation:
        facts = (await repo.relation_facts(viewer, [target]))[target]
        return FriendsService._relation_from(viewer, target, facts)

    @staticmethod
    def _relation_from(viewer: UUID, target: UUID, facts: RelationFacts) -> Relation:
        value = relation_of(
            viewer=viewer,
            target=target,
            target_exists=facts.nickname is not None,
            privacy=facts.privacy,
            is_friend=facts.is_friend,
            viewer_blocked_target=facts.viewer_blocked_target,
            target_blocked_viewer=facts.target_blocked_viewer,
            outgoing_pending=facts.outgoing_pending,
        )
        return replace(value, nickname=facts.nickname) if value.profile_visible else value

    # ------------------------------------------------------------------ присутствие
    async def heartbeat(self, user: UUID) -> None:
        """Отметить игрока онлайн (ключ живёт ``presence.ttl_s``); частые вызовы - 429.

        Raises:
            PresenceRateLimitedError: больше ``presence_burst`` вызовов за ``heartbeat_s``.
        """
        cfg = await current_config(self._config)
        window = await self._limiter.hit(f"presence:{user}", cfg.presence.heartbeat_s)
        if window.count > self._presence_burst:
            raise PresenceRateLimitedError(
                "слишком частые heartbeat", details={"retry_after": window.resets_in_s}
            )
        await self._presence.touch(user, cfg.presence.ttl_s)

    # ------------------------------------------------------------------ приглашения
    async def _online_banned(self, user: UUID, *, claim: bool) -> bool:
        """Санкция ONLINE: объединение claim токена и действующих записей ``sanctions``.

        Таблица social ловит санкцию, выданную после выпуска токена; claim остаётся запасным,
        если чтение недоступно (ADR 0052). Цена объединения: снятая санкция держит приглашения
        закрытыми до истечения токена (≤ access TTL) - приглашение не критично для безопасности.
        """
        if claim or self._sanctions is None:
            return claim
        try:
            mask = await self._sanctions.active_mask(user)
        except Exception:
            log.warning("social.sanctions_unavailable", exc_info=True)
            return False
        return bool(mask & Sanction.ONLINE)

    async def invite(
        self, user: UUID, friend: UUID, room_code: str, *, key: str, online_banned: bool
    ) -> InviteOutcome:
        """Пригласить друга в комнату.

        Raises:
            InviteForbiddenError: санкция ONLINE, не друг или блокировка в любую сторону.
            InvitesRateLimitedError: больше ``invites_per_minute`` в минуту.
            ProfileNotReadyError: нет ника отправителя в проекции.
        """
        if user == friend or await self._online_banned(user, claim=online_banned):
            raise InviteForbiddenError("приглашения недоступны")
        cfg = await current_config(self._config)
        fingerprint = request_fingerprint("invite.send", friend, {"room_code": room_code})
        async with self._uow() as uow:

            async def perform() -> Json:
                repo = uow.repo
                if (
                    not await repo.are_friends(user, friend)
                    or await repo.blocked(user, friend)
                    or await repo.blocked(friend, user)
                ):
                    raise InviteForbiddenError("приглашать можно только друзей")
                window = await self._limiter.hit(f"invite:{user}", INVITE_WINDOW_S)
                if window.count > cfg.friends.invites_per_minute:
                    raise InvitesRateLimitedError(
                        "слишком много приглашений", details={"retry_after": window.resets_in_s}
                    )
                nick = await repo.nickname(user)
                if nick is None:
                    raise ProfileNotReadyError("профиль ещё не готов, повторите позже")
                invite_id = uuid7()
                uow.publish(
                    RoomInviteSent(
                        aggregate_id=str(invite_id),
                        invite_id=invite_id,
                        from_user_id=user,
                        from_nickname=nick,
                        to_user_id=friend,
                        room_code=room_code,
                        at=self._clock.now(),
                    )
                )
                return {"invite_id": str(invite_id)}

            response, executed = await run_idempotent(
                uow.idempotency, user, key, fingerprint, perform, status_code=202
            )
            await uow.commit()
        return InviteOutcome(UUID(str(response["invite_id"])), repeated=not executed)

    # ------------------------------------------------------------------ лимит перебора
    async def _check_lookup(self, cfg: SocialConfig, sender: UUID, client_ip: str | None) -> None:
        limit = cfg.friends.code_attempts_per_hour
        keys = [f"lookup:user:{sender}"]
        if client_ip is not None:
            keys.append(f"lookup:ip:{client_ip}")
        for key in keys:
            window = await self._limiter.hit(key, LOOKUP_WINDOW_S)
            if window.count > limit:
                raise LookupRateLimitedError(
                    "слишком много попыток найти игрока",
                    details={"retry_after": window.resets_in_s},
                )
