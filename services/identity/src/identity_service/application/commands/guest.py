"""Гостевые сессии: выдача, завершение при входе в аккаунт, очистка (D5 плана этапа 2).

Гость получает обычную web-сессию identity без пользователя: access-токен без прав
(``gst = true``), refresh с общей ротацией. Ни одна ручка, которой нужен аккаунт или право,
гостя не пропускает (``require_user``/``require_perm`` в ``gv_auth``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError
from identity_service.application.commands.auth import revoke_quietly
from identity_service.application.commands.guest_issuer import GuestConfig, GuestSessionIssuer
from identity_service.application.ports import (
    GuestSessionPurger,
    IdentityUnitOfWork,
    RateLimiter,
    RevocationStore,
    UnitOfWorkFactory,
)
from identity_service.application.security import NO_IP, ensure_allowed, hash_secret, rate_key
from identity_service.application.sessions import IssuedSession
from identity_service.domain.guest import GUEST_UPGRADED_REASON

log = structlog.get_logger(__name__)

GUEST_WINDOW_S = 3600


def _already_authenticated() -> ConflictError:
    return ConflictError("сессия уже открыта", code="auth.already_authenticated")


class StartGuestSession:
    """``POST /v1/auth/guest``: новая гостевая сессия.

    Не выдаётся поверх действующей сессии (пользователя или гостя): новые cookie молча
    затёрли бы её, и пользователь с истёкшим access-токеном оказался бы гостем.
    Лимит — по IP клиента (fail-open вместе с лимитером: отказ Redis не выключает игру,
    а гостевой токен без прав ничего не открывает).
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        issuer: GuestSessionIssuer,
        limiter: RateLimiter,
        clock: Clock,
        config: GuestConfig,
        *,
        on_started: Callable[[], object] | None = None,
    ) -> None:
        self._uow, self._issuer, self._limiter = uow, issuer, limiter
        self._clock, self._config, self._on_started = clock, config, on_started

    async def __call__(
        self, *, ip: str | None, refresh_token: str | None, authenticated: bool
    ) -> IssuedSession:
        """Открыть гостевую сессию.

        Args:
            ip: IP клиента (ключ лимита; без адреса — общий счётчик «без IP»).
            refresh_token: Refresh-токен из cookie запроса, если есть.
            authenticated: Запрос несёт действующий access-токен.

        Raises:
            ConflictError: ``auth.already_authenticated`` — сессия уже есть.
            RateLimitedError: превышен лимит выдачи с IP.
        """
        if authenticated:
            raise _already_authenticated()
        ensure_allowed(
            await self._limiter.hit(
                rate_key("guest:ip", ip or NO_IP),
                limit=self._config.per_ip_per_hour,
                window_s=GUEST_WINDOW_S,
            )
        )
        async with self._uow() as uow:
            if refresh_token and await self._has_live_session(uow, refresh_token):
                raise _already_authenticated()
            issued = await self._issuer.open(uow, ip=ip)
            await uow.commit()
        if self._on_started is not None:
            self._on_started()
        log.info("auth.guest_session_started", session_id=str(issued.session_id))
        return issued

    async def _has_live_session(self, uow: IdentityUnitOfWork, refresh_token: str) -> bool:
        """Refresh-токен из cookie ещё может обновить действующую сессию."""
        token = await uow.sessions.find_refresh_for_update(hash_secret(refresh_token))
        if token is None or token.used_at is not None or token.expires_at <= self._clock.now():
            return False
        session = await uow.sessions.get(token.session_id)
        return session is not None and session.is_active


class EndGuestSession:
    """Отозвать гостевую сессию браузера, вошедшего в аккаунт (D5: вход отзывает гостя).

    Трогает только гостевые сессии: чужой или пользовательский refresh-токен, попавший сюда,
    ничего не отзывает. Идемпотентно.
    """

    def __init__(self, uow: UnitOfWorkFactory, revocation: RevocationStore, clock: Clock) -> None:
        self._uow, self._revocation, self._clock = uow, revocation, clock

    async def __call__(
        self, *, refresh_token: str | None = None, session_id: UUID | None = None
    ) -> bool:
        """Отозвать гостевую сессию по refresh-токену или ``sid``. ``True`` — отозвана."""
        async with self._uow() as uow:
            if session_id is None and refresh_token:
                token = await uow.sessions.find_refresh_for_update(hash_secret(refresh_token))
                session_id = token.session_id if token else None
            session = await uow.sessions.get(session_id) if session_id else None
            if session is None or not session.is_guest:
                return False
            if not session.revoke(self._clock.now(), GUEST_UPGRADED_REASON):
                return False
            await uow.sessions.save(session)
            await uow.commit()
        await revoke_quietly(self._revocation, [session.id])
        log.info("auth.guest_session_upgraded", session_id=str(session.id))
        return True


class PurgeGuestSessions:
    """Периодическая задача: удалить заброшенные и давно отозванные гостевые сессии.

    Правило — ``domain.guest.is_abandoned``; удаление пачками, не больше
    ``purge_max_batches`` за запуск (большой хвост дочищается следующими запусками).
    """

    def __init__(self, purger: GuestSessionPurger, clock: Clock, config: GuestConfig) -> None:
        self._purger, self._clock, self._config = purger, clock, config

    async def __call__(self) -> int:
        """Удалить; возвращает число удалённых сессий."""
        now = self._clock.now()
        idle_before = now - timedelta(seconds=self._config.refresh_ttl_s)
        revoked_before = now - timedelta(seconds=self._config.revoked_retention_s)
        total = 0
        for _ in range(self._config.purge_max_batches):
            deleted = await self._purger.purge(
                idle_before=idle_before,
                revoked_before=revoked_before,
                limit=self._config.purge_batch,
            )
            total += deleted
            if deleted < self._config.purge_batch:
                break
        log.info("auth.guest_sessions_purged", deleted=total)
        return total


@dataclass(frozen=True)
class GuestUseCases:
    """Use-cases гостевых сессий (часть ``IdentityUseCases``)."""

    config: GuestConfig
    start: StartGuestSession
    end: EndGuestSession
