"""Репозитории notifications на SQLAlchemy Core и Unit of Work.

UoW работает в одном из двух режимов: своя сессия и транзакция (API, воркер) или SAVEPOINT в
транзакции consumer (отметка inbox и записи коммитятся атомарно).
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Row,
    String,
    Uuid,
    and_,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_db.idempotency import SqlIdempotencyStore
from notifications_service.application.ports import AdminAction, Broadcast
from notifications_service.domain.notification import (
    Device,
    Notification,
    Subscription,
    TypePreference,
)
from notifications_service.infrastructure.db import tables as t


def _notification(row: Row[Any]) -> Notification:
    r = row  # Row: доступ по именам колонок
    return Notification(
        id=r.id,
        user_id=r.user_id,
        type_key=r.type,
        dedup_key=r.dedup_key,
        title=r.title,
        body=r.body,
        created_at=r.created_at,
        read_at=r.read_at,
        data=dict(r.data or {}),
    )


class SqlNotifications:
    """Уведомления."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, notification: Notification) -> bool:
        stmt = (
            pg_insert(t.notifications)
            .values(
                id=notification.id,
                user_id=notification.user_id,
                type=notification.type_key,
                dedup_key=notification.dedup_key,
                title=notification.title,
                body=notification.body,
                created_at=notification.created_at,
                read_at=notification.read_at,
                data=dict(notification.data),
            )
            .on_conflict_do_nothing(constraint="uq_notifications_user_dedup")
            .returning(t.notifications.c.id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def page(
        self,
        user_id: UUID,
        *,
        unread_only: bool,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Notification]:
        n = t.notifications
        stmt = select(n).where(n.c.user_id == user_id)
        if unread_only:
            stmt = stmt.where(n.c.read_at.is_(None))
        if after is not None:
            at, row_id = after
            stmt = stmt.where(or_(n.c.created_at < at, and_(n.c.created_at == at, n.c.id < row_id)))
        stmt = stmt.order_by(n.c.created_at.desc(), n.c.id.desc()).limit(limit)
        return [_notification(row) for row in (await self._s.execute(stmt)).all()]

    async def unread_count(self, user_id: UUID) -> int:
        n = t.notifications
        stmt = (
            select(func.count()).select_from(n).where(n.c.user_id == user_id, n.c.read_at.is_(None))
        )
        return int(await self._s.scalar(stmt) or 0)

    async def mark_read(self, user_id: UUID, notification_id: UUID, now: datetime) -> bool:
        n = t.notifications
        stmt = (
            update(n)
            .where(n.c.id == notification_id, n.c.user_id == user_id)
            .values(read_at=func.coalesce(n.c.read_at, now))
            .returning(n.c.id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def mark_all_read(self, user_id: UUID, now: datetime) -> int:
        n = t.notifications
        stmt = (
            update(n)
            .where(n.c.user_id == user_id, n.c.read_at.is_(None))
            .values(read_at=now)
            .returning(n.c.id)
        )
        return len((await self._s.execute(stmt)).all())

    async def known_users_after(self, after: UUID | None, limit: int) -> list[UUID]:
        n = t.notifications
        p = t.notification_preferences
        known = select(n.c.user_id).union(select(p.c.user_id)).subquery("known")
        stmt = select(known.c.user_id)
        if after is not None:
            stmt = stmt.where(known.c.user_id > after)
        stmt = stmt.order_by(known.c.user_id).limit(limit)
        return [row[0] for row in (await self._s.execute(stmt)).all()]


class SqlFinishedRuns:
    """Завершённые запуски событий."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def mark(self, run_id: UUID, reason: str, at: datetime) -> None:
        """Запомнить завершение (повтор не меняет первую запись)."""
        stmt = (
            pg_insert(t.finished_event_runs)
            .values(run_id=run_id, reason=reason, finished_at=at)
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        await self._s.execute(stmt)

    async def is_finished(self, run_id: UUID) -> bool:
        """Запуск завершён (``ended``/``stopped``)."""
        r = t.finished_event_runs
        return (await self._s.scalar(select(r.c.run_id).where(r.c.run_id == run_id))) is not None


class SqlPreferences:
    """Настройки по типам."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def all(self, user_id: UUID) -> dict[str, TypePreference]:
        p = t.notification_preferences
        rows = (await self._s.execute(select(p).where(p.c.user_id == user_id))).all()
        return {r.type: TypePreference(in_app=r.in_app, email=r.email) for r in rows}

    async def save(self, user_id: UUID, type_key: str, pref: TypePreference, now: datetime) -> None:
        p = t.notification_preferences
        stmt = pg_insert(p).values(
            user_id=user_id, type=type_key, in_app=pref.in_app, email=pref.email, updated_at=now
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[p.c.user_id, p.c.type],
            set_={"in_app": pref.in_app, "email": pref.email, "updated_at": now},
        )
        await self._s.execute(stmt)


class SqlSubscriptions:
    """Согласие на рассылку."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> Subscription | None:
        e = t.email_subscriptions
        row = (await self._s.execute(select(e).where(e.c.user_id == user_id))).first()
        return None if row is None else Subscription(row.subscribed, row.changed_at)

    async def save(self, user_id: UUID, *, subscribed: bool, now: datetime) -> None:
        e = t.email_subscriptions
        stmt = pg_insert(e).values(user_id=user_id, subscribed=subscribed, changed_at=now)
        # changed_at двигается только при реальной смене значения.
        stmt = stmt.on_conflict_do_update(
            index_elements=[e.c.user_id],
            set_={"subscribed": subscribed, "changed_at": now},
            where=e.c.subscribed != subscribed,
        )
        await self._s.execute(stmt)

    async def unsubscribe(self, user_id: UUID, now: datetime) -> None:
        e = t.email_subscriptions
        await self._s.execute(
            update(e)
            .where(e.c.user_id == user_id, e.c.subscribed.is_(True))
            .values(subscribed=False, changed_at=now)
        )

    async def count_subscribed(self) -> int:
        e = t.email_subscriptions
        stmt = select(func.count()).select_from(e).where(e.c.subscribed.is_(True))
        return int(await self._s.scalar(stmt) or 0)

    async def undelivered_after(
        self, broadcast_id: UUID, after: UUID | None, limit: int
    ) -> list[UUID]:
        e = t.email_subscriptions
        d = t.broadcast_deliveries
        done = (
            select(1).where(d.c.broadcast_id == broadcast_id, d.c.user_id == e.c.user_id).exists()
        )
        stmt = select(e.c.user_id).where(e.c.subscribed.is_(True), ~done)
        if after is not None:
            stmt = stmt.where(e.c.user_id > after)
        stmt = stmt.order_by(e.c.user_id).limit(limit)
        return [row[0] for row in (await self._s.execute(stmt)).all()]


class SqlDevices:
    """Устройства Push."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def register(
        self, device_id: UUID, user_id: UUID, platform: str, token_hash: str, now: datetime
    ) -> Device:
        d = t.devices
        # Тот же токен - то же устройство (id сохраняется); устройство переходит к тому, кто
        # предъявил токен.
        stmt = (
            pg_insert(d)
            .values(
                id=device_id,
                user_id=user_id,
                platform=platform,
                token_hash=token_hash,
                registered_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_devices_platform_token",
                set_={"user_id": user_id, "deleted_at": None},  # удалённое устройство оживает
            )
            .returning(d.c.id, d.c.platform, d.c.registered_at)
        )
        row = (await self._s.execute(stmt)).one()
        return Device(id=row.id, platform=row.platform, registered_at=row.registered_at)

    async def delete(self, user_id: UUID, device_id: UUID, now: datetime) -> bool:
        # Мягкое удаление: запись о своём удалённом устройстве отличает повтор (204) от
        # чужого или никогда не существовавшего (404).
        d = t.devices
        stmt = (
            update(d)
            .where(d.c.id == device_id, d.c.user_id == user_id)
            .values(deleted_at=func.coalesce(d.c.deleted_at, now))
            .returning(d.c.id)
        )
        return (await self._s.execute(stmt)).first() is not None


def _broadcast(row: Row[Any]) -> Broadcast:
    r = row
    return Broadcast(
        id=r.id,
        subject=r.subject,
        body=r.body,
        status=r.status,
        recipients=r.recipients,
        delivered=r.delivered,
        failed=r.failed,
        created_at=r.created_at,
        created_by=r.created_by,
    )


class SqlBroadcasts:
    """Рассылки и факты отправки."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, broadcast: Broadcast) -> None:
        await self._s.execute(
            insert(t.broadcasts).values(
                id=broadcast.id,
                subject=broadcast.subject,
                body=broadcast.body,
                status=broadcast.status,
                recipients=broadcast.recipients,
                delivered=broadcast.delivered,
                failed=broadcast.failed,
                created_at=broadcast.created_at,
                created_by=broadcast.created_by,
            )
        )

    async def get(self, broadcast_id: UUID, *, lock: bool = False) -> Broadcast | None:
        b = t.broadcasts
        stmt = select(b).where(b.c.id == broadcast_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _broadcast(row)

    async def page(self, *, after: tuple[datetime, UUID] | None, limit: int) -> list[Broadcast]:
        b = t.broadcasts
        stmt = select(b)
        if after is not None:
            at, row_id = after
            stmt = stmt.where(or_(b.c.created_at < at, and_(b.c.created_at == at, b.c.id < row_id)))
        stmt = stmt.order_by(b.c.created_at.desc(), b.c.id.desc()).limit(limit)
        return [_broadcast(row) for row in (await self._s.execute(stmt)).all()]

    async def set_status(self, broadcast_id: UUID, status: str) -> None:
        b = t.broadcasts
        await self._s.execute(update(b).where(b.c.id == broadcast_id).values(status=status))

    async def add_counts(self, broadcast_id: UUID, *, delivered: int, failed: int) -> None:
        b = t.broadcasts
        await self._s.execute(
            update(b)
            .where(b.c.id == broadcast_id)
            .values(delivered=b.c.delivered + delivered, failed=b.c.failed + failed)
        )

    async def mark_failed(self, broadcast_id: UUID, user_id: UUID) -> None:
        d = t.broadcast_deliveries
        await self._s.execute(
            update(d)
            .where(d.c.broadcast_id == broadcast_id, d.c.user_id == user_id)
            .values(status="failed")
        )

    async def claim(
        self,
        broadcast_id: UUID,
        user_id: UUID,
        now: datetime,
        *,
        token_hash: str,
        token_expires_at: datetime,
    ) -> bool:
        # Занятие только для подписанного прямо сейчас: отписка до отправки письмо отменяет.
        e = t.email_subscriptions
        source = select(
            literal(broadcast_id, Uuid(as_uuid=True)),
            e.c.user_id,
            literal(now, DateTime(timezone=True)),
            literal(token_hash, String(64)),
            literal(token_expires_at, DateTime(timezone=True)),
        ).where(e.c.user_id == user_id, e.c.subscribed.is_(True))
        stmt = (
            pg_insert(t.broadcast_deliveries)
            .from_select(
                ["broadcast_id", "user_id", "sent_at", "token_hash", "token_expires_at"], source
            )
            .on_conflict_do_nothing()
            .returning(t.broadcast_deliveries.c.user_id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def token_owner(self, token_hash: str, now: datetime) -> UUID | None:
        d = t.broadcast_deliveries
        stmt = select(d.c.user_id).where(d.c.token_hash == token_hash, d.c.token_expires_at > now)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def release(self, broadcast_id: UUID, user_id: UUID) -> None:
        d = t.broadcast_deliveries
        await self._s.execute(
            delete(d).where(d.c.broadcast_id == broadcast_id, d.c.user_id == user_id)
        )


class SqlAudit:
    """Аудит admin-действий."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, action: AdminAction) -> None:
        await self._s.execute(
            insert(t.admin_actions).values(
                id=action.id,
                actor_id=action.actor_id,
                action=action.action,
                target=action.target,
                reason=action.reason,
                details=action.details,
                at=action.at,
            )
        )


class SqlUnitOfWork:
    """Транзакция use-case: своя сессия (``sessions``) или SAVEPOINT в чужой (``session``)."""

    notifications: SqlNotifications
    preferences: SqlPreferences
    finished_runs: SqlFinishedRuns
    subscriptions: SqlSubscriptions
    devices: SqlDevices
    broadcasts: SqlBroadcasts
    audit: SqlAudit
    idempotency: SqlIdempotencyStore

    def __init__(
        self,
        clock: Clock,
        *,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if (sessions is None) == (session is None):
            raise ValueError("нужен ровно один из sessions и session")
        self._clock = clock
        self._factory = sessions
        self._outer = session
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        """Сессия транзакции."""
        if self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        return self._session

    async def __aenter__(self) -> Self:
        if self._outer is not None:
            self._session = self._outer
            self._tx = await self._outer.begin_nested()
        else:
            assert self._factory is not None  # noqa: S101 — проверено в __init__
            self._session = self._factory()
            self._tx = await self._session.begin()
        s = self._session
        self.notifications = SqlNotifications(s)
        self.preferences = SqlPreferences(s)
        self.finished_runs = SqlFinishedRuns(s)
        self.subscriptions = SqlSubscriptions(s)
        self.devices = SqlDevices(s)
        self.broadcasts = SqlBroadcasts(s)
        self.audit = SqlAudit(s)
        self.idempotency = SqlIdempotencyStore(s, t.idempotency_keys, self._clock)
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
            if self._outer is None and self._session is not None:
                await self._session.close()

    async def commit(self) -> None:
        """Зафиксировать транзакцию (или SAVEPOINT)."""
        if self._tx is None:
            raise RuntimeError("Unit of Work не открыт")
        await self._tx.commit()
        self._committed = True
