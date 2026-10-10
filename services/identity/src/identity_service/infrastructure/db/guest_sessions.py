"""Очистка гостевых сессий в Postgres (порт ``GuestSessionPurger``).

Refresh-токены удаляются каскадом (``refresh_tokens.session_id ON DELETE CASCADE``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity_service.domain.guest import SubjectKind
from identity_service.infrastructure.db.tables import sessions


class SqlGuestSessionPurger:
    """Пачечный ``DELETE`` по частичному индексу ``ix_sessions_guest_last_seen``.

    Условие повторяет ``domain.guest.is_abandoned``; ``FOR UPDATE SKIP LOCKED`` не мешает
    сессии, которую прямо сейчас ротирует refresh (она не заброшена — пропускаем).
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def purge(self, *, idle_before: datetime, revoked_before: datetime, limit: int) -> int:
        """Удалить до ``limit`` заброшенных гостевых сессий."""
        victims = (
            select(sessions.c.id)
            .where(
                sessions.c.subject_kind == SubjectKind.GUEST.value,
                or_(
                    sessions.c.last_seen_at <= idle_before,
                    sessions.c.revoked_at <= revoked_before,
                ),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with self._factory() as session, session.begin():
            result = await session.execute(delete(sessions).where(sessions.c.id.in_(victims)))
        return int(getattr(result, "rowcount", 0) or 0)
