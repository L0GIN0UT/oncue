"""Проекция ``social.UserSanctionsChanged`` в match (ADR 0052).

Чтение — отдельной короткой сессией (решение о доступе); запись — статический метод с сессией
транзакции ``EventProcessor`` (inbox и проекция атомарны). Применяется только событие с
большей ``version``: дубль и переупорядочивание не меняют проекцию.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_auth import parse_entries
from match_service.domain.sanctions import SanctionsSnapshot
from match_service.infrastructure.db.tables import sanctions_proj

SANCTIONS_UNAVAILABLE = Counter(
    "gv_match_sanctions_projection_failures_total",
    "Сбои чтения проекции санкций при решении о доступе",
)


class SqlSanctions:
    """``SanctionsProjection`` на PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None) -> None:
        self._sessions = sessions

    async def get(self, user_id: UUID) -> SanctionsSnapshot | None:
        """Снимок игрока или ``None``."""
        if self._sessions is None:
            raise RuntimeError("база данных не настроена")
        t = sanctions_proj
        async with self._sessions() as session:
            row = (await session.execute(select(t).where(t.c.user_id == user_id))).first()
        if row is None:
            return None
        return SanctionsSnapshot(
            user_id=row.user_id,
            version=row.version,
            entries=parse_entries(row.active),
            mask=row.mask,
            next_expiry_at=row.next_expiry_at,
        )

    @staticmethod
    async def apply(session: AsyncSession, snapshot: SanctionsSnapshot, now: datetime) -> bool:
        """Сохранить, если ``version`` строго новее; ``False`` - событие устарело."""
        t = sanctions_proj
        values = {
            "mask": snapshot.mask,
            "version": snapshot.version,
            "next_expiry_at": snapshot.next_expiry_at,
            "active": [
                {
                    "kind": e.kind,
                    "expires_at": e.expires_at.isoformat() if e.expires_at else None,
                }
                for e in snapshot.entries
            ],
            "updated_at": now,
        }
        stmt = (
            insert(t)
            .values(user_id=snapshot.user_id, **values)
            .on_conflict_do_update(
                index_elements=[t.c.user_id], set_=values, where=t.c.version < snapshot.version
            )
            .returning(t.c.user_id)
        )
        return (await session.execute(stmt)).first() is not None
