"""Выпуск токенов гостевой сессии: открытие и ротация refresh (D5 плана этапа 2).

Отдельно от ``guest.py``: ``RefreshSession`` (``auth.py``) выдаёт гостю новую пару через
этот модуль, а ``guest.py`` сам зависит от ``auth.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from gv_core.clock import Clock
from gv_core.ids import uuid7
from identity_service.application.ports import GuestTokenIssuer, IdentityUnitOfWork
from identity_service.application.security import ip_prefix
from identity_service.application.sessions import IssuedSession, SessionIssuer
from identity_service.domain import RefreshToken, Session
from identity_service.domain.guest import GUEST_ROLE, open_guest_session


@dataclass(frozen=True, slots=True)
class GuestConfig:
    """Параметры гостевых сессий (значения — ``GV_GUEST__*``).

    Attributes:
        per_ip_per_hour: Новых гостевых сессий с одного IP в час.
        refresh_ttl_s: Время жизни refresh-токена гостя (скользящее, как у пользователя).
        revoked_retention_s: Сколько хранить отозванную гостевую сессию до удаления.
        purge_batch: Сессий за один ``DELETE`` задачи очистки.
        purge_max_batches: Пачек за один запуск задачи (остальное — в следующий запуск).
    """

    per_ip_per_hour: int = 20
    refresh_ttl_s: int = 2_592_000
    revoked_retention_s: int = 86_400
    purge_batch: int = 1000
    purge_max_batches: int = 20


class GuestSessionIssuer:
    """Гостевая сессия + refresh-токен + access-токен без прав."""

    def __init__(
        self,
        tokens: GuestTokenIssuer,
        sessions: SessionIssuer,
        clock: Clock,
        *,
        refresh_ttl_s: int,
    ) -> None:
        self._tokens, self._sessions, self._clock = tokens, sessions, clock
        self._refresh_ttl = timedelta(seconds=refresh_ttl_s)

    @property
    def refresh_ttl(self) -> timedelta:
        """Время жизни refresh-токена гостя."""
        return self._refresh_ttl

    async def open(self, uow: IdentityUnitOfWork, *, ip: str | None) -> IssuedSession:
        """Создать гостевую сессию в транзакции ``uow`` (коммит — за вызывающим)."""
        now = self._clock.now()
        session = open_guest_session(
            session_id=uuid7(), guest_id=uuid7(), now=now, ip_prefix=ip_prefix(ip)
        )
        secret, token_hash = self._sessions.new_refresh_secret()
        await uow.sessions.add(session)
        await uow.sessions.add_refresh(
            RefreshToken(
                id=uuid7(),
                session_id=session.id,
                token_hash=token_hash,
                parent_id=None,
                created_at=now,
                expires_at=now + self._refresh_ttl,
            )
        )
        return self.access(session, refresh_secret=secret)

    def access(self, session: Session, *, refresh_secret: str) -> IssuedSession:
        """Выпустить access-токен гостя для сессии ``session``.

        Raises:
            ValueError: сессия не гостевая (ошибка вызывающего кода).
        """
        if session.guest_id is None:
            raise ValueError("сессия не гостевая")
        issued = self._tokens.issue_guest_access(guest_id=session.guest_id, session_id=session.id)
        return IssuedSession(
            session_id=session.id,
            user_id=session.guest_id,
            access_token=issued.token,
            expires_in=issued.expires_in,
            refresh_token=refresh_secret,
            roles=frozenset({GUEST_ROLE}),
            perms=frozenset(),
            display_name="",
            identity_version=0,
            is_guest=True,
            refresh_ttl_s=int(self._refresh_ttl.total_seconds()),
        )
