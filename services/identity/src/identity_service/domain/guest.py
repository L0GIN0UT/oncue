"""Гостевая сессия (D5 плана этапа 2): игра без аккаунта.

Гость — это сессия identity без пользователя: ``guest_id`` (uuid7) становится ``sub``
access-токена, ролей кроме ``guest`` и прав нет. Ротация refresh и отзыв — общие с
пользовательскими сессиями (``domain/session.py``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from identity_service.domain.session import Session, SessionKind

GUEST_ROLE = "guest"
"""Единственная роль гостевого токена (для отображения; решения — по ``gst`` и правам)."""

GUEST_UPGRADED_REASON = "guest_upgraded"
"""Причина отзыва гостевой сессии при входе в аккаунт из того же браузера."""


class SubjectKind(StrEnum):
    """Чья сессия: пользователя или гостя (колонка ``sessions.subject_kind``)."""

    USER = "user"
    GUEST = "guest"


def subject_kind(session: Session) -> SubjectKind:
    """Вид субъекта сессии."""
    return SubjectKind.GUEST if session.is_guest else SubjectKind.USER


def open_guest_session(
    *, session_id: UUID, guest_id: UUID, now: datetime, ip_prefix: str | None
) -> Session:
    """Новая гостевая web-сессия.

    ``last_seen_at`` выставляется сразу: по нему чистятся заброшенные сессии.
    """
    return Session(
        id=session_id,
        user_id=None,
        guest_id=guest_id,
        kind=SessionKind.WEB,
        created_at=now,
        last_seen_at=now,
        ip_prefix=ip_prefix,
    )


def is_abandoned(session: Session, *, now: datetime, idle_s: int, revoked_retention_s: int) -> bool:
    """Гостевую сессию можно удалить.

    * Простаивает дольше срока refresh-токена: каждая ротация двигает ``last_seen_at`` и
      выдаёт токен на ``idle_s``, значит все её refresh-токены уже истекли.
    * Отозвана раньше ``revoked_retention_s``: строка нужна только чтобы отличать повторное
      использование refresh от неизвестного токена — исход для клиента одинаков (401).
    """
    if not session.is_guest:
        return False
    seen = session.last_seen_at or session.created_at
    if (now - seen).total_seconds() >= idle_s:
        return True
    return (
        session.revoked_at is not None
        and (now - session.revoked_at).total_seconds() >= revoked_retention_s
    )
