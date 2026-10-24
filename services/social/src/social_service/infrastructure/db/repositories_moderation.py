"""Репозитории комментариев, жалоб и проекций social на SQLAlchemy Core и Unit of Work.

UoW работает в двух режимах: своя сессия и транзакция (API) или SAVEPOINT в транзакции
consumer, где уже стоит отметка inbox (коммит всей транзакции делает ``EventProcessor``).
События пишутся в outbox той же транзакции.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Row, and_, column, func, insert, literal, or_, select, table, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import DomainEvent
from gv_db.idempotency import SqlIdempotencyStore
from gv_messaging import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from social_service.application.ports.discussion import NewReport
from social_service.domain.discussion import CollectionRef, Comment
from social_service.infrastructure.db import tables_moderation as t
from social_service.infrastructure.db.tables import TABLES

# Проекция пользователей ведёт поток друзей; здесь только чтение ников.
_users_proj = table("users_proj", column("user_id"), column("nickname"))

_COUNTER_FIELDS = frozenset(
    {"forfeits", "leaves", "tech_losses", "reports_received", "praises", "matches_total"}
)


def _comment(row: Row[Any]) -> Comment:
    r = row
    return Comment(
        id=r.id,
        collection_id=r.collection_id,
        author_id=r.author_id,
        body=r.body,
        created_at=r.created_at,
        edited_at=r.edited_at,
        deleted_at=r.deleted_at,
        deleted_by=r.deleted_by,
        deleted_role=r.deleted_role,
    )


class SqlComments:
    """Комментарии."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, comment: Comment) -> None:
        await self._s.execute(
            insert(t.comments).values(
                id=comment.id,
                collection_id=comment.collection_id,
                author_id=comment.author_id,
                body=comment.body,
                created_at=comment.created_at,
            )
        )

    async def get(self, comment_id: UUID) -> Comment | None:
        row = (
            await self._s.execute(select(t.comments).where(t.comments.c.id == comment_id))
        ).first()
        return _comment(row) if row else None

    async def set_body(self, comment_id: UUID, body: str, edited_at: datetime) -> None:
        await self._s.execute(
            update(t.comments)
            .where(t.comments.c.id == comment_id)
            .values(body=body, edited_at=edited_at)
        )

    async def mark_deleted(
        self, comment_id: UUID, *, at: datetime, by: UUID | None, role: str
    ) -> bool:
        c = t.comments
        result = await self._s.execute(
            update(c)
            .where(c.c.id == comment_id, c.c.deleted_at.is_(None))
            .values(body="", deleted_at=at, deleted_by=by, deleted_role=role)
            .returning(c.c.id)
        )
        return result.first() is not None

    async def page(
        self, collection_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Comment]:
        c = t.comments
        stmt = select(c).where(c.c.collection_id == collection_id, c.c.deleted_at.is_(None))
        if after is not None:
            at, row_id = after
            stmt = stmt.where(or_(c.c.created_at < at, and_(c.c.created_at == at, c.c.id < row_id)))
        stmt = stmt.order_by(c.c.created_at.desc(), c.c.id.desc()).limit(limit)
        return [_comment(r) for r in (await self._s.execute(stmt)).all()]

    async def count(self, collection_id: UUID) -> int:
        c = t.comments
        stmt = (
            select(func.count())
            .select_from(c)
            .where(c.c.collection_id == collection_id, c.c.deleted_at.is_(None))
        )
        return int(await self._s.scalar(stmt) or 0)

    async def anonymize_author(self, user_id: UUID, *, at: datetime) -> list[Comment]:
        c = t.comments
        live = (
            await self._s.execute(
                select(c).where(c.c.author_id == user_id, c.c.deleted_at.is_(None))
            )
        ).all()
        if live:
            await self._s.execute(
                update(c)
                .where(c.c.author_id == user_id, c.c.deleted_at.is_(None))
                .values(body="", deleted_at=at, deleted_by=None, deleted_role="account_deleted")
            )
        return [_comment(r) for r in live]


class SqlCollectionProjection:
    """Проекция сборников."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, collection_id: UUID) -> CollectionRef | None:
        p = t.collections_proj
        row = (await self._s.execute(select(p).where(p.c.id == collection_id))).first()
        if row is None:
            return None
        return CollectionRef(
            id=row.id,
            author_id=row.author_id,
            title=row.title,
            published=row.status == "published",
            version=row.version,
        )

    async def apply(self, ref: CollectionRef) -> None:
        p = t.collections_proj
        values = {
            "author_id": ref.author_id,
            "title": ref.title,
            "status": "published" if ref.published else "unpublished",
            "version": ref.version,
        }
        stmt = pg_insert(p).values(id=ref.id, **values)
        # Событие старой версии (переупорядочивание, повтор) проекцию не откатывает.
        stmt = stmt.on_conflict_do_update(
            index_elements=[p.c.id], set_=values, where=p.c.version < ref.version
        )
        await self._s.execute(stmt)


class SqlNicknames:
    """Ники из проекции пользователей (если её ещё нет - пусто, комментарии не падают)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_ids: list[UUID]) -> dict[UUID, str]:
        if not user_ids:
            return {}
        try:
            async with self._s.begin_nested():
                rows = (
                    await self._s.execute(
                        select(_users_proj.c.user_id, _users_proj.c.nickname).where(
                            _users_proj.c.user_id.in_(user_ids)
                        )
                    )
                ).all()
        except ProgrammingError:
            return {}
        return {r.user_id: r.nickname for r in rows if r.nickname}


class SqlReports:
    """Жалобы и кейсы."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_target(self, kind: str, target_id: UUID) -> None:
        digest = hashlib.sha256(f"report:{kind}:{target_id}".encode()).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        await self._s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})

    async def insert(self, report: NewReport) -> UUID | None:
        r = t.reports
        stmt = (
            pg_insert(r)
            .values(
                id=report.id,
                author_id=report.author_id,
                target_kind=report.target_kind,
                target_id=report.target_id,
                target_user_id=report.target_user_id,
                context_match_id=report.context_match_id,
                reason_code=report.reason_code,
                text=report.text,
                created_at=report.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_reports_author_target")
            .returning(r.c.id)
        )
        row = (await self._s.execute(stmt)).first()
        return row.id if row else None

    async def existing_id(self, author_id: UUID, kind: str, target_id: UUID) -> UUID:
        r = t.reports
        stmt = select(r.c.id).where(
            r.c.author_id == author_id, r.c.target_kind == kind, r.c.target_id == target_id
        )
        return UUID(str(await self._s.scalar(stmt)))

    async def count_by_author_since(self, author_id: UUID, since: datetime) -> int:
        r = t.reports
        stmt = (
            select(func.count())
            .select_from(r)
            .where(r.c.author_id == author_id, r.c.created_at >= since)
        )
        return int(await self._s.scalar(stmt) or 0)

    async def recent_ids(self, kind: str, target_id: UUID, since: datetime) -> list[UUID]:
        r = t.reports
        stmt = select(r.c.id).where(
            r.c.target_kind == kind, r.c.target_id == target_id, r.c.created_at >= since
        )
        return [row.id for row in (await self._s.execute(stmt)).all()]

    async def open_case(
        self, case_id: UUID, kind: str, target_id: UUID, target_user_id: UUID, now: datetime
    ) -> UUID | None:
        c = t.moderation_cases
        stmt = (
            pg_insert(c)
            .values(
                id=case_id,
                target_kind=kind,
                target_id=target_id,
                target_user_id=target_user_id,
                status="open",
                opened_at=now,
                version=1,
            )
            .on_conflict_do_nothing(
                index_elements=[c.c.target_kind, c.c.target_id],
                index_where=c.c.status != "resolved",
            )
            .returning(c.c.id)
        )
        row = (await self._s.execute(stmt)).first()
        return row.id if row else None

    async def active_case_id(self, kind: str, target_id: UUID) -> UUID | None:
        c = t.moderation_cases
        stmt = select(c.c.id).where(
            c.c.target_kind == kind, c.c.target_id == target_id, c.c.status != "resolved"
        )
        return await self._s.scalar(stmt)

    async def attach(self, case_id: UUID, report_ids: list[UUID]) -> None:
        if not report_ids:
            return
        await self._s.execute(
            pg_insert(t.case_reports)
            .values([{"case_id": case_id, "report_id": rid} for rid in report_ids])
            .on_conflict_do_nothing()
        )

    async def anonymize_author(self, user_id: UUID) -> int:
        r = t.reports
        result = await self._s.execute(
            update(r).where(r.c.author_id == user_id, r.c.text.is_not(None)).values(text=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class SqlMatches:
    """Участники матчей и счётчики порядочности."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_participants(
        self, match_id: UUID, user_ids: list[UUID], *, solo: bool, finished_at: datetime
    ) -> list[UUID]:
        if not user_ids:
            return []
        m = t.match_participants_proj
        stmt = (
            pg_insert(m)
            .values(
                [
                    {"match_id": match_id, "user_id": u, "solo": solo, "finished_at": finished_at}
                    for u in user_ids
                ]
            )
            .on_conflict_do_nothing()
            .returning(m.c.user_id)
        )
        return [row.user_id for row in (await self._s.execute(stmt)).all()]

    async def shared_match(self, match_id: UUID, first: UUID, second: UUID) -> bool:
        m = t.match_participants_proj
        stmt = (
            select(func.count())
            .select_from(m)
            .where(m.c.match_id == match_id, m.c.solo.is_(False), m.c.user_id.in_([first, second]))
        )
        return int(await self._s.scalar(stmt) or 0) == (1 if first == second else 2)

    async def bump(self, user_id: UUID, field: str, by: int = 1) -> None:
        if field not in _COUNTER_FIELDS:
            raise ValueError(f"неизвестный счётчик {field}")
        b = t.behaviour_counters
        stmt = pg_insert(b).values(user_id=user_id, **{field: by})
        stmt = stmt.on_conflict_do_update(
            index_elements=[b.c.user_id], set_={field: b.c[field] + literal(by)}
        )
        await self._s.execute(stmt)


class SqlDiscussionUnitOfWork:
    """Транзакция use-case комментариев и жалоб с записью событий в outbox."""

    comments: SqlComments
    collections: SqlCollectionProjection
    nicknames: SqlNicknames
    reports: SqlReports
    matches: SqlMatches
    idempotency: SqlIdempotencyStore

    def __init__(
        self,
        *,
        producer: str,
        clock: Clock,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if (sessions is None) == (session is None):
            raise ValueError("нужен ровно один из sessions или session")
        self._sessions = sessions
        self._external = session
        self._outbox = OutboxWriter(TABLES.outbox)
        self._producer = producer
        self._clock = clock
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._events: list[DomainEvent] = []
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work одноразовый")
        if self._external is not None:
            self._session = self._external
            self._tx = await self._session.begin_nested()
        else:
            assert self._sessions is not None  # noqa: S101 - проверено в __init__
            self._session = self._sessions()
            self._tx = await self._session.begin()
        s = self._session
        self.comments = SqlComments(s)
        self.collections = SqlCollectionProjection(s)
        self.nicknames = SqlNicknames(s)
        self.reports = SqlReports(s)
        self.matches = SqlMatches(s)
        self.idempotency = SqlIdempotencyStore(s, t.COMMENT_IDEMPOTENCY, self._clock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed and self._tx is not None and self._tx.is_active:
                await self._tx.rollback()
        finally:
            if self._external is None and self._session is not None:
                await self._session.close()

    def emit(self, event: DomainEvent) -> None:
        self._events.append(event)

    async def commit(self) -> None:
        """События - в outbox, затем коммит (или RELEASE SAVEPOINT)."""
        if self._tx is None or self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        envelopes = [
            EventEnvelope.from_domain_event(e, producer=self._producer, clock=self._clock)
            for e in self._events
        ]
        self._events = []
        await self._outbox.add(self._session, envelopes)
        await self._tx.commit()
        self._committed = True
