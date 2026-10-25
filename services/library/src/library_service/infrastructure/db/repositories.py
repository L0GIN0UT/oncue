"""Репозитории library на PostgreSQL и Unit of Work с outbox."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Row, delete, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import AggregateRoot
from gv_db.idempotency import IdempotencyStore, SqlIdempotencyStore
from gv_messaging import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from library_service.application.ports import (
    LibraryRepository,
    ProjectionRepository,
    PurgeRepository,
    ShelfPageRow,
)
from library_service.domain.library import (
    CatalogEntry,
    ItemAdded,
    ItemDeleted,
    ItemStatus,
    PersonEntry,
    Shelf,
    ShelfCreated,
    ShelfDeleted,
    ShelfItem,
    UserLibrary,
)
from library_service.domain.shelves import ShelfKind
from library_service.infrastructure.db.tables import (
    TABLES,
    catalog_items_proj,
    idempotency_keys,
    shelf_items,
    shelves,
    user_purges,
    user_versions,
)


def _shelf(row: Row[Any]) -> Shelf:
    return Shelf(
        id=row.id,
        user_id=row.user_id,
        kind=ShelfKind(row.kind),
        key=row.key,
        title=row.title,
        items_count=row.items_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item(row: Row[Any]) -> ShelfItem:
    return ShelfItem(
        shelf_id=row.shelf_id,
        media_item_id=row.media_item_id,
        domain=row.domain,
        source=row.source,
        added_at=row.added_at,
    )


def _persons(raw: Any) -> tuple[PersonEntry, ...]:
    return tuple(
        PersonEntry(name=str(p["name"]), role=str(p["role"]), order=int(p["order"]))
        for p in raw or ()
    )


class SqlLibraryRepository:
    """``LibraryRepository``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_user(self, user_id: UUID, now: datetime) -> int:
        """Строка ``user_versions`` под ``FOR UPDATE`` (создаётся при первой мутации)."""
        await self._s.execute(
            pg_insert(user_versions)
            .values(user_id=user_id, version=0, updated_at=now)
            .on_conflict_do_nothing(index_elements=[user_versions.c.user_id])
        )
        version = (
            await self._s.execute(
                select(user_versions.c.version)
                .where(user_versions.c.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one()
        return int(version)

    async def shelves(self, user_id: UUID) -> list[Shelf]:
        """Все полки пользователя."""
        rows = await self._s.execute(select(shelves).where(shelves.c.user_id == user_id))
        return [_shelf(r) for r in rows]

    async def placements(self, user_id: UUID, media_item_id: UUID) -> list[ShelfItem]:
        """Строки единицы на полках пользователя (индекс ``(user_id, media_item_id)``)."""
        rows = await self._s.execute(
            select(shelf_items).where(
                shelf_items.c.user_id == user_id, shelf_items.c.media_item_id == media_item_id
            )
        )
        return [_item(r) for r in rows]

    async def placements_many(
        self, user_id: UUID, media_item_ids: Sequence[UUID]
    ) -> list[ShelfItem]:
        """Строки нескольких единиц пользователя одним запросом (тот же индекс)."""
        if not media_item_ids:
            return []
        rows = await self._s.execute(
            select(shelf_items).where(
                shelf_items.c.user_id == user_id, shelf_items.c.media_item_id.in_(media_item_ids)
            )
        )
        return [_item(r) for r in rows]

    async def shelf_items(self, shelf_id: UUID) -> list[ShelfItem]:
        """Все строки полки."""
        rows = await self._s.execute(select(shelf_items).where(shelf_items.c.shelf_id == shelf_id))
        return [_item(r) for r in rows]

    async def save(self, library: UserLibrary, now: datetime) -> None:
        """Изменения агрегата по порядку, затем счётчики затронутых полок и версия."""
        for change in library.changes:
            match change:
                case ShelfCreated(shelf=s):
                    await self._s.execute(
                        insert(shelves).values(
                            id=s.id,
                            user_id=s.user_id,
                            kind=str(s.kind),
                            key=s.key,
                            title=s.title,
                            items_count=0,
                            created_at=s.created_at,
                            updated_at=s.updated_at,
                        )
                    )
                case ItemAdded(item=i):
                    await self._s.execute(
                        insert(shelf_items).values(
                            shelf_id=i.shelf_id,
                            media_item_id=i.media_item_id,
                            user_id=library.user_id,
                            domain=i.domain,
                            source=i.source,
                            added_at=i.added_at,
                        )
                    )
                case ItemDeleted(shelf_id=shelf_id, media_item_id=media_item_id):
                    await self._s.execute(
                        delete(shelf_items).where(
                            shelf_items.c.shelf_id == shelf_id,
                            shelf_items.c.media_item_id == media_item_id,
                        )
                    )
                case ShelfDeleted(shelf_id=shelf_id):
                    await self._s.execute(delete(shelves).where(shelves.c.id == shelf_id))
        for shelf_id in library.touched:
            s = library.shelves[shelf_id]
            await self._s.execute(
                update(shelves)
                .where(shelves.c.id == shelf_id)
                .values(items_count=s.items_count, title=s.title, updated_at=s.updated_at)
            )
        await self._s.execute(
            update(user_versions)
            .where(user_versions.c.user_id == library.user_id)
            .values(version=library.version, updated_at=now)
        )
        library.changes.clear()
        library.touched.clear()

    async def page(
        self, shelf_id: UUID, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[ShelfPageRow]:
        """Страница полки: новые сверху (индекс ``(shelf_id, added_at, media_item_id)``)."""
        p = catalog_items_proj
        query = (
            select(
                shelf_items,
                p.c.version.label("p_version"),
                p.c.kind,
                p.c.status,
                p.c.title,
                p.c.year,
                p.c.persons,
                p.c.domain.label("p_domain"),
            )
            .select_from(shelf_items.outerjoin(p, p.c.media_item_id == shelf_items.c.media_item_id))
            .where(shelf_items.c.shelf_id == shelf_id)
            .order_by(shelf_items.c.added_at.desc(), shelf_items.c.media_item_id.desc())
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(shelf_items.c.added_at, shelf_items.c.media_item_id) < tuple_(*after)
            )
        result: list[ShelfPageRow] = []
        for row in await self._s.execute(query):
            entry = None
            if row.p_version is not None:
                entry = CatalogEntry(
                    media_item_id=row.media_item_id,
                    domain=row.p_domain,
                    kind=row.kind,
                    status=ItemStatus(row.status),
                    title=row.title,
                    year=row.year,
                    persons=_persons(row.persons),
                    version=row.p_version,
                )
            result.append(ShelfPageRow(_item(row), entry))
        return result


class SqlProjectionRepository:
    """``ProjectionRepository``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, media_item_id: UUID, *, for_update: bool = False) -> CatalogEntry | None:
        """Единица из проекции."""
        query = select(catalog_items_proj).where(
            catalog_items_proj.c.media_item_id == media_item_id
        )
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).first()
        if row is None:
            return None
        return CatalogEntry(
            media_item_id=row.media_item_id,
            domain=row.domain,
            kind=row.kind,
            status=ItemStatus(row.status),
            title=row.title,
            year=row.year,
            persons=_persons(row.persons),
            version=row.version,
        )

    async def upsert(self, entry: CatalogEntry, now: datetime) -> None:
        """Полный снимок."""
        values = {
            "version": entry.version,
            "domain": entry.domain,
            "kind": entry.kind,
            "status": str(entry.status),
            "title": entry.title,
            "year": entry.year,
            "persons": [{"name": p.name, "role": p.role, "order": p.order} for p in entry.persons],
            "updated_at": now,
        }
        await self._s.execute(
            pg_insert(catalog_items_proj)
            .values(media_item_id=entry.media_item_id, **values)
            .on_conflict_do_update(index_elements=[catalog_items_proj.c.media_item_id], set_=values)
        )

    async def set_status(
        self, media_item_id: UUID, status: ItemStatus, version: int, now: datetime
    ) -> None:
        """Статус и версия."""
        await self._s.execute(
            update(catalog_items_proj)
            .where(catalog_items_proj.c.media_item_id == media_item_id)
            .values(status=str(status), version=version, updated_at=now)
        )


class SqlPurgeRepository:
    """``PurgeRepository``: порция — не больше ``batch`` строк ``shelf_items``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def request(self, user_id: UUID, now: datetime) -> None:
        """Отметка удаления (повтор — no-op)."""
        await self._s.execute(
            pg_insert(user_purges)
            .values(user_id=user_id, requested_at=now)
            .on_conflict_do_nothing(index_elements=[user_purges.c.user_id])
        )

    async def pending(self, limit: int) -> list[UUID]:
        """Отмеченные пользователи, старые первыми."""
        rows = await self._s.execute(
            select(user_purges.c.user_id)
            .order_by(user_purges.c.requested_at, user_purges.c.user_id)
            .limit(limit)
        )
        return list(rows.scalars())

    async def delete_batch(self, user_id: UUID, batch: int) -> int:
        """Сначала единицы порцией; когда их не осталось — полки, версия и ключи
        идемпотентности (в сохранённых ответах есть названия полок)."""
        chunk = (
            select(shelf_items.c.shelf_id, shelf_items.c.media_item_id)
            .where(shelf_items.c.user_id == user_id)
            .limit(batch)
        )
        deleted = await self._s.execute(
            delete(shelf_items).where(
                tuple_(shelf_items.c.shelf_id, shelf_items.c.media_item_id).in_(chunk)
            )
        )
        count = _rowcount(deleted)
        if count:
            return count
        for stmt in (
            delete(shelves).where(shelves.c.user_id == user_id),
            delete(user_versions).where(user_versions.c.user_id == user_id),
            delete(idempotency_keys).where(idempotency_keys.c.actor_id == user_id),
        ):
            count += _rowcount(await self._s.execute(stmt))
        return count

    async def finish(self, user_id: UUID) -> None:
        """Снять отметку."""
        await self._s.execute(delete(user_purges).where(user_purges.c.user_id == user_id))


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class SqlLibraryUnitOfWork:
    """Транзакция use-case с репозиториями и записью событий в outbox.

    Два режима: своя транзакция (API, задачи) или SAVEPOINT в транзакции consumer, где уже
    стоит отметка inbox (коммит всей транзакции делает ``EventProcessor``).
    """

    library: LibraryRepository
    projection: ProjectionRepository
    purges: PurgeRepository
    idempotency: IdempotencyStore

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
        self._tracked: list[AggregateRoot[Any]] = []
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work одноразовый")
        if self._external is not None:
            self._session = self._external
            self._tx = await self._session.begin_nested()
        else:
            assert self._sessions is not None  # noqa: S101 — проверено в __init__
            self._session = self._sessions()
            self._tx = await self._session.begin()
        s = self._session
        self.library = SqlLibraryRepository(s)
        self.projection = SqlProjectionRepository(s)
        self.purges = SqlPurgeRepository(s)
        self.idempotency = SqlIdempotencyStore(s, idempotency_keys, self._clock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            if self._external is None and self._session is not None:
                await self._session.close()

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отметить агрегат: его события попадут в outbox при коммите."""
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        """События — в outbox, затем коммит (или RELEASE SAVEPOINT)."""
        if self._tx is None or self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        envelopes = [
            EventEnvelope.from_domain_event(event, producer=self._producer, clock=self._clock)
            for aggregate in self._tracked
            for event in aggregate.pull_events()
        ]
        await self._outbox.add(self._session, envelopes)
        await self._tx.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Откатить (транзакцию или SAVEPOINT)."""
        if self._tx is not None and self._tx.is_active:
            await self._tx.rollback()
        for aggregate in self._tracked:
            aggregate.pull_events()
