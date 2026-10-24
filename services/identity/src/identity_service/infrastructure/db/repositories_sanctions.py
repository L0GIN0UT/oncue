"""Санкции игроков в БД identity (проекция ``social.UserSanctionsChanged``, ADR 0052)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gv_auth import SanctionEntry, parse_entries
from identity_service.domain.user_sanctions import UserSanctions
from identity_service.infrastructure.db.tables import user_sanctions


def _dump(entries: tuple[SanctionEntry, ...]) -> list[dict[str, Any]]:
    return [
        {"kind": e.kind, "expires_at": e.expires_at.isoformat() if e.expires_at else None}
        for e in entries
    ]


class SqlUserSanctionRepository:
    """Снимки санкций; обновляются только событием с большей ``version``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> UserSanctions | None:
        t = user_sanctions
        row = (await self._s.execute(select(t).where(t.c.user_id == user_id))).first()
        if row is None:
            return None
        return UserSanctions(
            user_id=row.user_id,
            version=row.version,
            entries=parse_entries(row.active),
            mask=row.mask,
            account_banned=row.account_banned,
            next_expiry_at=row.next_expiry_at,
            updated_at=row.updated_at,
        )

    async def apply(self, snapshot: UserSanctions) -> bool:
        t = user_sanctions
        values = {
            "mask": snapshot.mask,
            "account_banned": snapshot.account_banned,
            "version": snapshot.version,
            "next_expiry_at": snapshot.next_expiry_at,
            "active": _dump(snapshot.entries),
            "updated_at": snapshot.updated_at,
        }
        stmt = (
            pg_insert(t)
            .values(user_id=snapshot.user_id, **values)
            .on_conflict_do_update(
                index_elements=[t.c.user_id], set_=values, where=t.c.version < snapshot.version
            )
            .returning(t.c.user_id)
        )
        return (await self._s.execute(stmt)).first() is not None
