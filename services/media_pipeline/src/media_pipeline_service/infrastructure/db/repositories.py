"""Репозитории PostgreSQL (SQLAlchemy Core) и Unit of Work сервиса."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.context import current_context
from gv_core.domain import AggregateRoot
from gv_core.errors import ConflictError
from gv_core.ids import uuid7
from gv_db.idempotency import IdempotencyStore, SqlIdempotencyStore
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from media_pipeline_service.application.ports import (
    AdminAction,
    AdminAuditLog,
    AssetSetRepository,
    DynamicProfileRepository,
    LinkIssuanceJournal,
    MediaItemStateRepository,
    ProfileSetCount,
    SourceMediaRepository,
)
from media_pipeline_service.domain.asset_set import AssetLevel, AssetSet, AssetSetStatus
from media_pipeline_service.domain.dynamic_profiles import DynamicProfile, DynamicProfileStatus
from media_pipeline_service.domain.errors import MediaItemNotFoundError
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.sources import (
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
)
from media_pipeline_service.infrastructure.db.tables import (
    TABLES,
    admin_actions,
    asset_levels,
    asset_profiles_dynamic,
    asset_sets,
    idempotency_keys,
    link_issuance,
    media_items_state,
    source_media,
)


# ---------------------------------------------------------------- наборы ассетов
def _level_row(asset_set_id: UUID, level: AssetLevel) -> dict[str, Any]:
    return {
        "asset_set_id": asset_set_id,
        "level": level.level,
        "object_key": level.object_key,
        "mime": level.mime,
        "bytes": level.size_bytes,
        "duration_ms": level.duration_ms,
        "width": level.width,
        "height": level.height,
    }


def _set_row(aggregate: AssetSet) -> dict[str, Any]:
    return {
        "media_item_id": aggregate.media_item_id,
        "clue_type": aggregate.clue_type,
        "profile_key": aggregate.profile_key,
        "params": aggregate.params,
        "params_hash": aggregate.params_hash,
        "levels_count": aggregate.levels_count,
        "source_media_id": aggregate.source_media_id,
        "source_media_ids": list(aggregate.source_media_ids),
        "status": aggregate.status.value,
        "generation": aggregate.generation,
        "attempts": aggregate.attempts,
        "error_code": aggregate.error_code,
        "version": aggregate.version,
        "created_at": aggregate.created_at,
        "updated_at": aggregate.updated_at,
    }


def _source_ids(row: RowMapping) -> tuple[UUID, ...]:
    # Наборы до 0005: только source_media_id (может стать NULL по ON DELETE SET NULL).
    ids = row["source_media_ids"]
    if ids:
        return tuple(ids)
    single = row["source_media_id"]
    return (single,) if single is not None else ()


def _to_set(row: RowMapping, levels: Sequence[RowMapping]) -> AssetSet:
    return AssetSet(
        row["id"],
        media_item_id=row["media_item_id"],
        clue_type=row["clue_type"],
        profile_key=row["profile_key"],
        params=row["params"],
        params_hash=row["params_hash"],
        levels_count=row["levels_count"],
        source_media_ids=_source_ids(row),
        status=AssetSetStatus(row["status"]),
        generation=row["generation"],
        attempts=row["attempts"],
        error_code=row["error_code"],
        levels=[
            AssetLevel(
                level=lvl["level"],
                object_key=lvl["object_key"],
                mime=lvl["mime"],
                size_bytes=lvl["bytes"],
                duration_ms=lvl["duration_ms"],
                width=lvl["width"],
                height=lvl["height"],
            )
            for lvl in sorted(levels, key=lambda r: int(r["level"]))
        ],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqlAssetSetRepository:
    """Наборы ассетов и их уровни."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _load(self, rows: Sequence[RowMapping]) -> list[AssetSet]:
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        level_rows = (
            (
                await self._s.execute(
                    select(asset_levels).where(asset_levels.c.asset_set_id.in_(ids))
                )
            )
            .mappings()
            .all()
        )
        by_set: dict[UUID, list[RowMapping]] = {}
        for lvl in level_rows:
            by_set.setdefault(lvl["asset_set_id"], []).append(lvl)
        return [_to_set(r, by_set.get(r["id"], [])) for r in rows]

    async def get(self, asset_set_id: UUID, *, for_update: bool = False) -> AssetSet | None:
        """Набор по id."""
        query = select(asset_sets).where(asset_sets.c.id == asset_set_id)
        if for_update:
            query = query.with_for_update()
        rows = (await self._s.execute(query)).mappings().all()
        loaded = await self._load(rows)
        return loaded[0] if loaded else None

    async def list_for_item(
        self, media_item_id: UUID, clue_type: str | None = None, *, for_update: bool = False
    ) -> list[AssetSet]:
        """Наборы единицы по времени создания."""
        query = (
            select(asset_sets)
            .where(asset_sets.c.media_item_id == media_item_id)
            .order_by(asset_sets.c.created_at, asset_sets.c.id)
        )
        if clue_type is not None:
            query = query.where(asset_sets.c.clue_type == clue_type)
        if for_update:
            query = query.with_for_update()
        return await self._load((await self._s.execute(query)).mappings().all())

    async def add(self, aggregate: AssetSet) -> None:
        """Вставить новый набор."""
        await self._s.execute(insert(asset_sets).values(id=aggregate.id, **_set_row(aggregate)))
        await self._replace_levels(aggregate)
        aggregate.persisted_version = aggregate.version

    async def save(self, aggregate: AssetSet) -> None:
        """Обновить с проверкой версии; уровни перезаписываются целиком."""
        result = await self._s.execute(
            update(asset_sets)
            .where(
                asset_sets.c.id == aggregate.id,
                asset_sets.c.version == aggregate.persisted_version,
            )
            .values(**_set_row(aggregate))
            .returning(asset_sets.c.id)
        )
        if result.first() is None:
            raise ConflictError(
                "набор ассетов изменён параллельно", code="asset_set.concurrent_update"
            )
        await self._replace_levels(aggregate)
        aggregate.persisted_version = aggregate.version

    async def count_by_profile(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[ProfileSetCount]:
        """Группировка наборов по профилю и состоянию (индекс ``(clue_type, params_hash)``)."""
        c = asset_sets.c
        query = select(
            c.clue_type, c.profile_key, c.params_hash, c.status, func.count().label("n")
        ).group_by(c.clue_type, c.profile_key, c.params_hash, c.status)
        if clue_type is not None:
            query = query.where(c.clue_type == clue_type)
        if params_hash is not None:
            query = query.where(c.params_hash == params_hash)
        rows = (await self._s.execute(query)).all()
        return [
            ProfileSetCount(
                clue_type=r.clue_type,
                profile_key=r.profile_key,
                params_hash=r.params_hash,
                status=r.status,
                count=int(r.n),
            )
            for r in rows
        ]

    async def _replace_levels(self, aggregate: AssetSet) -> None:
        await self._s.execute(
            delete(asset_levels).where(asset_levels.c.asset_set_id == aggregate.id)
        )
        if aggregate.levels:
            await self._s.execute(
                insert(asset_levels), [_level_row(aggregate.id, lvl) for lvl in aggregate.levels]
            )


# ---------------------------------------------------------------- исходники
def _to_source(row: RowMapping) -> SourceMedia:
    return SourceMedia(
        row["id"],
        media_item_id=row["media_item_id"],
        kind=SourceKind(row["kind"]),
        origin=SourceOrigin(row["origin"]),
        uri=row["uri"],
        created_at=row["created_at"],
        status=SourceStatus(row["status"]),
        object_key=row["object_key"],
        sha256=row["sha256"],
        size_bytes=row["bytes"],
        mime=row["mime"],
        position=row["position"],
        source_url=row["source_url"],
        license_note=row["license_note"],
        text_content=row["text_content"],
    )


def _source_row(source: SourceMedia) -> dict[str, Any]:
    return {
        "media_item_id": source.media_item_id,
        "kind": source.kind.value,
        "origin": source.origin.value,
        "uri": source.uri,
        "status": source.status.value,
        "object_key": source.object_key,
        "sha256": source.sha256,
        "bytes": source.size_bytes,
        "mime": source.mime,
        "created_at": source.created_at,
        "position": source.position,
        "source_url": source.source_url,
        "license_note": source.license_note,
        "text_content": source.text_content,
    }


class SqlSourceMediaRepository:
    """Исходники."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, source_id: UUID) -> SourceMedia | None:
        """Исходник по id."""
        row = (
            (await self._s.execute(select(source_media).where(source_media.c.id == source_id)))
            .mappings()
            .first()
        )
        return _to_source(row) if row else None

    async def list_for_item(self, media_item_id: UUID) -> list[SourceMedia]:
        """Исходники единицы."""
        rows = (
            (
                await self._s.execute(
                    select(source_media)
                    .where(source_media.c.media_item_id == media_item_id)
                    .order_by(source_media.c.created_at)
                )
            )
            .mappings()
            .all()
        )
        return [_to_source(r) for r in rows]

    async def add(self, source: SourceMedia) -> None:
        """Вставить (повтор того же id — не ошибка: события приходят повторно)."""
        await self._s.execute(
            pg_insert(source_media)
            .values(id=source.id, **_source_row(source))
            .on_conflict_do_nothing(index_elements=[source_media.c.id])
        )

    async def save(self, source: SourceMedia) -> None:
        """Обновить."""
        await self._s.execute(
            update(source_media).where(source_media.c.id == source.id).values(**_source_row(source))
        )

    async def delete(self, source_ids: Sequence[UUID]) -> None:
        """Удалить исходники (наборы теряют ссылку: ``ON DELETE SET NULL``)."""
        if source_ids:
            await self._s.execute(delete(source_media).where(source_media.c.id.in_(source_ids)))


# ---------------------------------------------------------------- проекция единиц
class SqlMediaItemStateRepository:
    """Проекция единиц каталога."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, media_item_id: UUID, *, for_update: bool = False) -> MediaItemState | None:
        """Проекция по id."""
        query = select(media_items_state).where(media_items_state.c.media_item_id == media_item_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        if row is None:
            return None
        return MediaItemState(
            row["media_item_id"],
            domain=row["domain"],
            kind=row["kind"],
            status=ItemStatus(row["status"]),
            version=row["version"],
            updated_at=row["updated_at"],
        )

    async def upsert(self, state: MediaItemState) -> None:
        """Создать или обновить; на гонке применяется только большая версия."""
        values = {
            "media_item_id": state.id,
            "domain": state.domain,
            "kind": state.kind,
            "status": state.status.value,
            "version": state.version,
            "updated_at": state.updated_at,
        }
        stmt = pg_insert(media_items_state).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[media_items_state.c.media_item_id],
                set_={k: stmt.excluded[k] for k in values if k != "media_item_id"},
                where=media_items_state.c.version < stmt.excluded.version,
            )
        )

    async def list_active_ids(self, domain: str, *, after: UUID | None, limit: int) -> list[UUID]:
        """id активных единиц домена после ``after`` (по первичному ключу)."""
        c = media_items_state.c
        query = (
            select(c.media_item_id)
            .where(c.domain == domain, c.status == ItemStatus.ACTIVE.value)
            .order_by(c.media_item_id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(c.media_item_id > after)
        return list((await self._s.execute(query)).scalars().all())

    async def next_event_version(self, media_item_id: UUID) -> int:
        """Увеличить счётчик версий событий единицы и вернуть новое значение.

        ``UPDATE … RETURNING`` берёт блокировку строки до конца транзакции: параллельные
        транзакции по той же единице получают версии строго по порядку коммита.
        """
        result = await self._s.execute(
            update(media_items_state)
            .where(media_items_state.c.media_item_id == media_item_id)
            .values(events_version=media_items_state.c.events_version + 1)
            .returning(media_items_state.c.events_version)
        )
        value = result.scalar_one_or_none()
        if value is None:
            raise MediaItemNotFoundError("медиа-единица не найдена")
        return int(value)


# ---------------------------------------------------------------- динамические профили
# Фиксированный ключ advisory lock заведения динамических профилей (ASCII «mP_D»).
_PROFILES_LOCK_KEY = 0x6D505F44


def _to_profile(row: RowMapping) -> DynamicProfile:
    return DynamicProfile(
        row["id"],
        clue_type=row["clue_type"],
        domain=row["domain"],
        params=row["params"],
        params_hash=row["params_hash"],
        status=DynamicProfileStatus(row["status"]),
        error_code=row["error_code"],
        request_id=row["request_id"],
        mode_key=row["mode_key"],
        actor_id=row["actor_id"],
        backfill_cursor=row["backfill_cursor"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _profile_row(profile: DynamicProfile) -> dict[str, Any]:
    return {
        "clue_type": profile.clue_type,
        "domain": profile.domain,
        "params": profile.params,
        "params_hash": profile.params_hash,
        "status": profile.status.value,
        "error_code": profile.error_code,
        "request_id": profile.request_id,
        "mode_key": profile.mode_key,
        "actor_id": profile.actor_id,
        "backfill_cursor": profile.backfill_cursor,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


class SqlDynamicProfileRepository:
    """Динамические профили."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock(self) -> None:
        """``pg_advisory_xact_lock``: проверка лимита и вставка — без гонки реплик."""
        await self._s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PROFILES_LOCK_KEY})

    async def get(
        self, clue_type: str, params_hash: str, domain: str, *, for_update: bool = False
    ) -> DynamicProfile | None:
        """Профиль по ключу дедупликации."""
        c = asset_profiles_dynamic.c
        query = select(asset_profiles_dynamic).where(
            c.clue_type == clue_type, c.params_hash == params_hash, c.domain == domain
        )
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        return _to_profile(row) if row else None

    async def count_usable(self) -> int:
        """Неотклонённые профили."""
        c = asset_profiles_dynamic.c
        query = select(func.count()).where(c.status != DynamicProfileStatus.REJECTED.value)
        return int((await self._s.execute(query)).scalar_one())

    async def add(self, profile: DynamicProfile) -> None:
        """Вставить."""
        await self._s.execute(
            insert(asset_profiles_dynamic).values(id=profile.id, **_profile_row(profile))
        )

    async def save(self, profile: DynamicProfile) -> None:
        """Обновить."""
        await self._s.execute(
            update(asset_profiles_dynamic)
            .where(asset_profiles_dynamic.c.id == profile.id)
            .values(**_profile_row(profile))
        )

    async def _list(self, *conditions: Any, limit: int | None = None) -> list[DynamicProfile]:
        c = asset_profiles_dynamic.c
        query = select(asset_profiles_dynamic).where(*conditions).order_by(c.created_at, c.id)
        if limit is not None:
            query = query.limit(limit)
        return [_to_profile(r) for r in (await self._s.execute(query)).mappings().all()]

    async def list_usable(self, domain: str) -> list[DynamicProfile]:
        """Неотклонённые профили домена."""
        c = asset_profiles_dynamic.c
        return await self._list(c.domain == domain, c.status != DynamicProfileStatus.REJECTED.value)

    async def list_backfilling(self, limit: int) -> list[DynamicProfile]:
        """Профили на досоздании."""
        c = asset_profiles_dynamic.c
        return await self._list(c.status == DynamicProfileStatus.BACKFILLING.value, limit=limit)

    async def list_all(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[DynamicProfile]:
        """Все профили с фильтрами."""
        c = asset_profiles_dynamic.c
        conditions: list[Any] = []
        if clue_type is not None:
            conditions.append(c.clue_type == clue_type)
        if params_hash is not None:
            conditions.append(c.params_hash == params_hash)
        return await self._list(*conditions)


# ---------------------------------------------------------------- журнал и аудит
class SqlLinkIssuanceJournal:
    """Журнал выдачи ссылок."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        asset_set_id: UUID,
        level: int,
        subject_hash: str,
        issued_at: datetime,
        expires_at: datetime,
        client_id: str | None = None,
    ) -> None:
        """Записать выдачу (субъект — только хэшем)."""
        await self._s.execute(
            insert(link_issuance).values(
                id=uuid7(),
                asset_set_id=asset_set_id,
                level=level,
                subject_hash=subject_hash,
                issued_at=issued_at,
                expires_at=expires_at,
                client_id=client_id,
            )
        )

    async def purge_older_than(self, cutoff: datetime, *, limit: int) -> int:
        """Удалить пачку старых записей (по индексу ``issued_at``)."""
        victims = (
            select(link_issuance.c.id).where(link_issuance.c.issued_at < cutoff).limit(limit)
        ).scalar_subquery()
        result = await self._s.execute(
            delete(link_issuance)
            .where(link_issuance.c.id.in_(victims))
            .returning(link_issuance.c.id)
        )
        return len(result.all())


class SqlAdminAuditLog:
    """Аудит admin-действий (append-only)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, action: AdminAction) -> None:
        """Записать действие с correlation_id текущего запроса."""
        ctx = current_context()
        await self._s.execute(
            insert(admin_actions).values(
                id=uuid7(),
                actor_id=action.actor_id,
                action=action.action,
                target_id=action.target_id,
                reason=action.reason,
                details=action.details,
                correlation_id=ctx.correlation_id if ctx else None,
                created_at=action.at,
            )
        )


# ---------------------------------------------------------------- Unit of Work
class SqlMediaUnitOfWork:
    """Транзакция use-case с репозиториями и записью событий в outbox.

    Два режима:
        * своя сессия из фабрики (API, воркер) — обычная транзакция;
        * чужая сессия (обработчик события в ``EventProcessor``) — SAVEPOINT внутри
          транзакции, где уже стоит отметка inbox. Коммит всей транзакции делает процессор,
          поэтому изменения и отметка inbox атомарны.
    """

    asset_sets: AssetSetRepository
    sources: SourceMediaRepository
    items: MediaItemStateRepository
    links: LinkIssuanceJournal
    profiles: DynamicProfileRepository
    idempotency: IdempotencyStore
    audit: AdminAuditLog

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
        self.asset_sets = SqlAssetSetRepository(s)
        self.sources = SqlSourceMediaRepository(s)
        self.items = SqlMediaItemStateRepository(s)
        self.links = SqlLinkIssuanceJournal(s)
        self.profiles = SqlDynamicProfileRepository(s)
        self.idempotency = SqlIdempotencyStore(s, idempotency_keys, self._clock)
        self.audit = SqlAdminAuditLog(s)
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
