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
from social_service.application.ports.cases import AuditEntry, AvatarRef, CaseReportRow
from social_service.application.ports.discussion import NewReport
from social_service.domain.cases import ModerationCase
from social_service.domain.discussion import CollectionRef, Comment
from social_service.infrastructure.db import tables_friends
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

    async def get(self, comment_id: UUID, *, for_update: bool = False) -> Comment | None:
        stmt = select(t.comments).where(t.comments.c.id == comment_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
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


class SqlBlocks:
    """Блокировки: чтение таблицы потока друзей (та же БД сервиса)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def is_blocked(self, blocker: UUID, blocked: UUID) -> bool:
        b = tables_friends.blocks
        stmt = select(b.c.blocker_id).where(b.c.blocker_id == blocker, b.c.blocked_id == blocked)
        return (await self._s.execute(stmt)).first() is not None


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

    async def unassigned_ids(self, kind: str, target_id: UUID, since: datetime) -> list[UUID]:
        r = t.reports
        attached = select(t.case_reports.c.report_id).where(t.case_reports.c.report_id == r.c.id)
        stmt = select(r.c.id).where(
            r.c.target_kind == kind,
            r.c.target_id == target_id,
            r.c.created_at >= since,
            ~attached.exists(),
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


def _case(row: Row[Any], reports_count: int) -> ModerationCase:
    return ModerationCase(
        id=row.id,
        target_kind=row.target_kind,
        target_id=row.target_id,
        target_user_id=row.target_user_id,
        status=row.status,
        assignee_id=row.assignee_id,
        opened_at=row.opened_at,
        version=row.version,
        reports_count=max(reports_count, 1),
        decision=row.decision,
        resolved_by=row.resolved_by,
        resolved_at=row.resolved_at,
    )


PREVIEW_MAX = 200


class SqlCases:
    """Кейсы модерации."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def _count(self) -> Any:  # Any: коррелированный подзапрос Core
        cr = t.case_reports
        return (
            select(func.count())
            .select_from(cr)
            .where(cr.c.case_id == t.moderation_cases.c.id)
            .scalar_subquery()
        )

    async def get(self, case_id: UUID, *, for_update: bool = False) -> ModerationCase | None:
        c = t.moderation_cases
        stmt = select(c, self._count().label("reports_count")).where(c.c.id == case_id)
        if for_update:
            stmt = stmt.with_for_update(of=c)
        row = (await self._s.execute(stmt)).first()
        return _case(row, row.reports_count) if row else None

    async def page(
        self,
        *,
        status: str | None,
        target_kind: str | None,
        assignee_id: UUID | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ModerationCase]:
        c = t.moderation_cases
        stmt = select(c, self._count().label("reports_count"))
        if status is not None:
            stmt = stmt.where(c.c.status == status)
        if target_kind is not None:
            stmt = stmt.where(c.c.target_kind == target_kind)
        if assignee_id is not None:
            stmt = stmt.where(c.c.assignee_id == assignee_id)
        if after is not None:
            at, row_id = after
            stmt = stmt.where(or_(c.c.opened_at > at, and_(c.c.opened_at == at, c.c.id > row_id)))
        stmt = stmt.order_by(c.c.opened_at, c.c.id).limit(limit)
        return [_case(r, r.reports_count) for r in (await self._s.execute(stmt)).all()]

    async def save(self, case: ModerationCase, *, expected_version: int) -> bool:
        c = t.moderation_cases
        result = await self._s.execute(
            update(c)
            .where(c.c.id == case.id, c.c.version == expected_version)
            .values(
                status=case.status,
                assignee_id=case.assignee_id,
                decision=case.decision,
                resolved_by=case.resolved_by,
                resolved_at=case.resolved_at,
                version=case.version,
            )
            .returning(c.c.id)
        )
        return result.first() is not None

    async def reports(self, case_id: UUID) -> list[CaseReportRow]:
        r, cr = t.reports, t.case_reports
        stmt = (
            select(r)
            .join(cr, cr.c.report_id == r.c.id)
            .where(cr.c.case_id == case_id)
            .order_by(r.c.created_at, r.c.id)
        )
        return [
            CaseReportRow(
                id=x.id,
                author_id=x.author_id,
                reason_code=x.reason_code,
                text=x.text,
                context_match_id=x.context_match_id,
                created_at=x.created_at,
            )
            for x in (await self._s.execute(stmt)).all()
        ]

    async def preview(self, kind: str, target_id: UUID) -> str | None:
        if kind == "comment":
            body = await self._s.scalar(
                select(t.comments.c.body).where(t.comments.c.id == target_id)
            )
            return (body or None) and body[:PREVIEW_MAX]
        if kind == "collection":
            title = await self._s.scalar(
                select(t.collections_proj.c.title).where(t.collections_proj.c.id == target_id)
            )
            return (title or None) and title[:PREVIEW_MAX]
        return None


class SqlAvatars:
    """Проекция владельцев аватарок."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, asset_id: UUID) -> AvatarRef | None:
        a = t.avatar_proj
        row = (await self._s.execute(select(a).where(a.c.asset_id == asset_id))).first()
        if row is None:
            return None
        return AvatarRef(row.asset_id, row.owner_user_id, row.status, row.version)

    async def apply(self, ref: AvatarRef) -> None:
        a = t.avatar_proj
        values = {"owner_user_id": ref.owner_user_id, "status": ref.status, "version": ref.version}
        stmt = pg_insert(a).values(asset_id=ref.asset_id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[a.c.asset_id], set_=values, where=a.c.version < ref.version
        )
        await self._s.execute(stmt)


class SqlStaffPerms:
    """Проекция прав сотрудников (``staff_perms_proj``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def apply(self, user_id: UUID, perms: frozenset[str], version: int) -> None:
        p = t.staff_perms_proj
        values = {"perms": sorted(perms), "version": version}
        stmt = pg_insert(p).values(user_id=user_id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[p.c.user_id], set_=values, where=p.c.version < version
        )
        await self._s.execute(stmt)

    async def delete(self, user_id: UUID) -> None:
        await self._s.execute(
            t.staff_perms_proj.delete().where(t.staff_perms_proj.c.user_id == user_id)
        )


class SqlAudit:
    """Аудит ручных действий."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, entry: AuditEntry) -> None:
        await self._s.execute(
            insert(t.moderation_audit).values(
                id=entry.id,
                actor_id=entry.actor_id,
                action=entry.action,
                target=entry.target,
                reason=entry.reason,
                details=entry.details,
                at=entry.at,
            )
        )


class SqlDiscussionUnitOfWork:
    """Транзакция use-case комментариев и жалоб с записью событий в outbox."""

    comments: SqlComments
    collections: SqlCollectionProjection
    nicknames: SqlNicknames
    reports: SqlReports
    matches: SqlMatches
    blocks: SqlBlocks
    cases: SqlCases
    avatars: SqlAvatars
    staff_perms: SqlStaffPerms
    audit: SqlAudit
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

    @property
    def session(self) -> AsyncSession:
        """Сессия открытой транзакции (для use-case, который должен писать в неё же)."""
        if self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        return self._session

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
        self.blocks = SqlBlocks(s)
        self.cases = SqlCases(s)
        self.avatars = SqlAvatars(s)
        self.staff_perms = SqlStaffPerms(s)
        self.audit = SqlAudit(s)
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
