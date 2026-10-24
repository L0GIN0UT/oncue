"""Санкции на SQLAlchemy Core и Unit of Work (поток F3, ADR 0052).

UoW наследует UoW друзей (те же outbox, ключи идемпотентности и два режима: своя транзакция
или SAVEPOINT в транзакции вызывающего - так кейс модерации (F4) решается атомарно с санкцией).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from social_service.application.ports.sanctions import SanctionKind, SanctionRecord
from social_service.application.ports.sanctions_store import CaseRow, UserCardData
from social_service.infrastructure.db import tables_friends
from social_service.infrastructure.db import tables_moderation as m
from social_service.infrastructure.db import tables_sanctions as t
from social_service.infrastructure.db.uow_friends import SqlFriendsUnitOfWork

_COUNTERS = ("forfeits", "leaves", "tech_losses", "reports_received", "praises", "matches_total")


def _record(row: Row[Any]) -> SanctionRecord:
    kind: SanctionKind = row.kind
    return SanctionRecord(
        id=row.id,
        user_id=row.user_id,
        kind=kind,
        started_at=row.started_at,
        expires_at=row.expires_at,
        reason=row.reason,
        actor_id=row.actor_id,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        revoke_reason=row.revoke_reason,
    )


class SqlSanctionsRepository:
    """Санкции и версия набора."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_and_bump(self, user_id: UUID) -> int:
        st = t.sanction_state
        stmt = (
            pg_insert(st)
            .values(user_id=user_id, version=1)
            .on_conflict_do_update(
                index_elements=[st.c.user_id], set_={"version": st.c.version + 1}
            )
            .returning(st.c.version)
        )
        return int(await self._s.scalar(stmt) or 0)

    async def get(self, sanction_id: UUID, *, for_update: bool = False) -> SanctionRecord | None:
        stmt = select(t.sanctions).where(t.sanctions.c.id == sanction_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return _record(row) if row else None

    async def open_of_kind(self, user_id: UUID, kind: str) -> SanctionRecord | None:
        s = t.sanctions
        stmt = select(s).where(s.c.user_id == user_id, s.c.kind == kind, s.c.revoked_at.is_(None))
        row = (await self._s.execute(stmt)).first()
        return _record(row) if row else None

    async def insert(self, record: SanctionRecord) -> None:
        await self._s.execute(
            insert(t.sanctions).values(
                id=record.id,
                user_id=record.user_id,
                kind=record.kind,
                started_at=record.started_at,
                expires_at=record.expires_at,
                reason=record.reason,
                actor_id=record.actor_id,
            )
        )

    async def close(self, sanction_id: UUID, *, at: datetime, by: UUID | None, reason: str) -> bool:
        s = t.sanctions
        result = await self._s.execute(
            update(s)
            .where(s.c.id == sanction_id, s.c.revoked_at.is_(None))
            .values(revoked_at=at, revoked_by=by, revoke_reason=reason)
            .returning(s.c.id)
        )
        return result.first() is not None

    async def active(self, user_id: UUID, now: datetime) -> list[SanctionRecord]:
        s = t.sanctions
        stmt = (
            select(s)
            .where(
                s.c.user_id == user_id,
                s.c.revoked_at.is_(None),
                (s.c.expires_at.is_(None)) | (s.c.expires_at > now),
            )
            .order_by(s.c.started_at, s.c.id)
        )
        return [_record(r) for r in (await self._s.execute(stmt)).all()]

    async def history(self, user_id: UUID, limit: int) -> list[SanctionRecord]:
        s = t.sanctions
        stmt = (
            select(s)
            .where(s.c.user_id == user_id)
            .order_by(s.c.started_at.desc(), s.c.id.desc())
            .limit(limit)
        )
        return [_record(r) for r in (await self._s.execute(stmt)).all()]

    async def users_with_due(self, now: datetime, limit: int) -> list[UUID]:
        s = t.sanctions
        stmt = (
            select(s.c.user_id)
            .where(s.c.revoked_at.is_(None), s.c.expires_at.is_not(None), s.c.expires_at <= now)
            .distinct()
            .limit(limit)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def due_of_user(self, user_id: UUID, now: datetime) -> list[SanctionRecord]:
        s = t.sanctions
        stmt = (
            select(s)
            .where(
                s.c.user_id == user_id,
                s.c.revoked_at.is_(None),
                s.c.expires_at.is_not(None),
                s.c.expires_at <= now,
            )
            .with_for_update()
        )
        return [_record(r) for r in (await self._s.execute(stmt)).all()]

    async def player_exists(self, user_id: UUID) -> bool:
        u = tables_friends.users_proj
        found = await self._s.scalar(select(u.c.user_id).where(u.c.user_id == user_id))
        return found is not None

    async def user_card(self, user_id: UUID, case_limit: int) -> UserCardData:
        u = tables_friends.users_proj
        nickname = await self._s.scalar(select(u.c.nickname).where(u.c.user_id == user_id))
        b = m.behaviour_counters
        row = (await self._s.execute(select(b).where(b.c.user_id == user_id))).first()
        counters = {name: int(getattr(row, name)) for name in _COUNTERS} if row else {}
        c, cr = m.moderation_cases, m.case_reports
        count = select(func.count()).select_from(cr).where(cr.c.case_id == c.c.id).scalar_subquery()
        stmt = (
            select(c, count.label("reports_count"))
            .where(c.c.target_user_id == user_id)
            .order_by(c.c.opened_at.desc(), c.c.id.desc())
            .limit(case_limit)
        )
        cases = [
            CaseRow(
                id=r.id,
                target_kind=r.target_kind,
                target_id=r.target_id,
                target_user_id=r.target_user_id,
                status=r.status,
                reports_count=max(int(r.reports_count), 1),
                assignee_id=r.assignee_id,
                opened_at=r.opened_at,
                version=r.version,
            )
            for r in (await self._s.execute(stmt)).all()
        ]
        return UserCardData(nickname=nickname, counters=counters, cases=cases)


class SqlSanctionsUnitOfWork(SqlFriendsUnitOfWork):
    """Транзакция санкций (``sanctions`` + outbox + ключи идемпотентности)."""

    sanctions: SqlSanctionsRepository

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        assert self._session is not None  # noqa: S101 - открыт родителем
        self.sanctions = SqlSanctionsRepository(self._session)
        return self
