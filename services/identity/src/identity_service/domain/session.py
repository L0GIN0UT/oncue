"""Сессии и refresh-токены.

Сессия — «семья» refresh-токенов: каждое использование выдаёт новый токен (ротация), а
повторное использование уже израсходованного — признак кражи, поэтому отзывается вся сессия
(RFC 6819 §5.2.2.3, OAuth 2.0 Security BCP §4.14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from gv_core.errors import UnauthenticatedError

REUSE_REASON = "refresh_reuse"


class SessionKind(StrEnum):
    """Канал входа: сайт (cookie) или админка (токены в теле ответа)."""

    WEB = "web"
    ADMIN = "admin"


@dataclass(slots=True)
class Session:
    """Сессия пользователя; ``id`` попадает в access-токен как ``sid``."""

    id: UUID
    user_id: UUID
    kind: SessionKind
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    ip_prefix: str | None = None

    @property
    def is_active(self) -> bool:
        """Сессия не отозвана."""
        return self.revoked_at is None

    def revoke(self, now: datetime, reason: str) -> bool:
        """Отозвать. ``False`` — уже была отозвана (причина не перезаписывается)."""
        if self.revoked_at is not None:
            return False
        self.revoked_at = now
        self.revoke_reason = reason
        return True


@dataclass(slots=True)
class RefreshToken:
    """Refresh-токен. В хранилище — только SHA-256 от секрета (``token_hash``)."""

    id: UUID
    session_id: UUID
    token_hash: str
    parent_id: UUID | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Rotated:
    """Ротация прошла: выдан новый токен."""

    token: RefreshToken


@dataclass(frozen=True, slots=True)
class ReuseDetected:
    """Израсходованный токен предъявлен повторно: сессия отозвана."""

    session_id: UUID


def rotate_refresh_token(
    session: Session,
    token: RefreshToken,
    *,
    now: datetime,
    new_id: UUID,
    new_hash: str,
    ttl: timedelta,
) -> Rotated | ReuseDetected:
    """Обменять refresh-токен на новый.

    Returns:
        ``Rotated`` с новым токеном или ``ReuseDetected`` (сессия уже отозвана — вызывающий
        обязан сохранить это и только потом отказать клиенту).

    Raises:
        UnauthenticatedError: сессия отозвана (``auth.session_revoked``) или токен истёк
            (``auth.refresh_expired``).
    """
    if token.session_id != session.id:
        raise ValueError("токен не принадлежит сессии")
    if not session.is_active:
        raise UnauthenticatedError("сессия завершена", code="auth.session_revoked")
    if token.used_at is not None:
        session.revoke(now, REUSE_REASON)
        return ReuseDetected(session.id)
    if token.expires_at <= now:
        raise UnauthenticatedError("refresh-токен истёк", code="auth.refresh_expired")
    token.used_at = now
    session.last_seen_at = now
    return Rotated(
        RefreshToken(
            id=new_id,
            session_id=session.id,
            token_hash=new_hash,
            parent_id=token.id,
            created_at=now,
            expires_at=now + ttl,
        )
    )
