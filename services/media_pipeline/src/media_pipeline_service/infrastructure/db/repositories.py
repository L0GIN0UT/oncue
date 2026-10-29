"""Репозитории PostgreSQL (SQLAlchemy Core) и Unit of Work сервиса."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import ColumnElement, Table, delete, func, insert, select, text, tuple_, update
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
    ANY_SCOPE,
    AdminAction,
    AdminAuditLog,
    AssetSetRepository,
    CollectionStateRepository,
    DynamicProfileRepository,
    ImportPackageRepository,
    LinkIssuanceJournal,
    MediaItemStateRepository,
    ProfileSetCount,
    ScopeFilter,
    SourceMediaRepository,
    UgcImageRepository,
)
from media_pipeline_service.domain.asset_set import AssetLevel, AssetSet, AssetSetStatus
from media_pipeline_service.domain.collections import CollectionState
from media_pipeline_service.domain.dynamic_profiles import DynamicProfile, DynamicProfileStatus
from media_pipeline_service.domain.errors import MediaItemNotFoundError
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.packages import ImportPackage, PackageFileError, PackageStatus
from media_pipeline_service.domain.sources import (
    AssetScope,
    ScopeKind,
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
)
from media_pipeline_service.domain.ugc import UgcImage, UgcStatus
from media_pipeline_service.infrastructure.db.tables import (
    TABLES,
    admin_actions,
    asset_levels,
    asset_profiles_dynamic,
    asset_sets,
    collections_state,
    idempotency_keys,
    import_package_errors,
    import_packages,
    link_issuance,
    media_items_state,
    source_media,
    ugc_images,
    ugc_object_purge,
)


# ---------------------------------------------------------------- владелец набора и исходника
def _scope_columns(scope: AssetScope | None) -> dict[str, Any]:
    if scope is None:
        return {"scope_kind": None, "scope_id": None}
    return {"scope_kind": scope.kind.value, "scope_id": scope.id}


def _scope_of(row: RowMapping) -> AssetScope | None:
    if row["scope_id"] is None:
        return None
    return AssetScope(ScopeKind(row["scope_kind"]), row["scope_id"])


def _scope_condition(table: Table, scope: AssetScope | None) -> ColumnElement[bool]:
    if scope is None:
        return table.c.scope_id.is_(None)
    return (table.c.scope_kind == scope.kind.value) & (table.c.scope_id == scope.id)


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
        **_scope_columns(aggregate.scope),
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
        scope=_scope_of(row),
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
        self,
        media_item_id: UUID,
        clue_type: str | None = None,
        *,
        for_update: bool = False,
        scope: ScopeFilter = ANY_SCOPE,
    ) -> list[AssetSet]:
        """Наборы единицы по времени создания (индекс ``media_item_id``)."""
        query = (
            select(asset_sets)
            .where(asset_sets.c.media_item_id == media_item_id)
            .order_by(asset_sets.c.created_at, asset_sets.c.id)
        )
        if clue_type is not None:
            query = query.where(asset_sets.c.clue_type == clue_type)
        if scope is not ANY_SCOPE:
            query = query.where(_scope_condition(asset_sets, scope))
        if for_update:
            query = query.with_for_update()
        return await self._load((await self._s.execute(query)).mappings().all())

    async def list_for_scope(
        self, scope: AssetScope, *, for_update: bool = False
    ) -> list[AssetSet]:
        """Наборы владельца (индекс ``uq_asset_sets_scope_profile``), по единицам."""
        query = (
            select(asset_sets)
            .where(_scope_condition(asset_sets, scope))
            .order_by(asset_sets.c.media_item_id, asset_sets.c.id)
        )
        if for_update:
            query = query.with_for_update()
        return await self._load((await self._s.execute(query)).mappings().all())

    async def delete(self, asset_set_ids: Sequence[UUID]) -> None:
        """Удалить наборы (уровни — ``ON DELETE CASCADE``)."""
        if asset_set_ids:
            await self._s.execute(delete(asset_sets).where(asset_sets.c.id.in_(asset_set_ids)))

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
        scope=_scope_of(row),
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
        **_scope_columns(source.scope),
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

    async def list_for_item(
        self, media_item_id: UUID, *, scope: AssetScope | None = None
    ) -> list[SourceMedia]:
        """Исходники единицы одного владельца."""
        rows = (
            (
                await self._s.execute(
                    select(source_media)
                    .where(
                        source_media.c.media_item_id == media_item_id,
                        _scope_condition(source_media, scope),
                    )
                    .order_by(source_media.c.created_at, source_media.c.id)
                )
            )
            .mappings()
            .all()
        )
        return [_to_source(r) for r in rows]

    async def list_unattributed(self, *, limit: int, after_id: UUID | None) -> list[SourceMedia]:
        """Файловые каталожные исходники без источника или лицензии, keyset по ``id``."""
        query = (
            select(source_media)
            .where(
                source_media.c.scope_kind.is_(None),
                source_media.c.kind != "text",
                (source_media.c.source_url.is_(None)) | (source_media.c.license_note.is_(None)),
            )
            .order_by(source_media.c.id)
            .limit(limit)
        )
        if after_id is not None:
            query = query.where(source_media.c.id > after_id)
        return [_to_source(r) for r in (await self._s.execute(query)).mappings().all()]

    async def list_for_scope(self, scope: AssetScope) -> list[SourceMedia]:
        """Исходники владельца (индекс ``uq_source_media_scope_item``)."""
        query = (
            select(source_media)
            .where(_scope_condition(source_media, scope))
            .order_by(source_media.c.media_item_id)
        )
        return [_to_source(r) for r in (await self._s.execute(query)).mappings().all()]

    async def scopes_for_item(self, media_item_id: UUID) -> list[AssetScope]:
        """Владельцы своих исходников единицы (индекс ``media_item_id``)."""
        c = source_media.c
        rows = (
            await self._s.execute(
                select(c.scope_kind, c.scope_id)
                .where(c.media_item_id == media_item_id, c.scope_id.is_not(None))
                .distinct()
                .order_by(c.scope_kind, c.scope_id)
            )
        ).all()
        return [AssetScope(ScopeKind(r.scope_kind), r.scope_id) for r in rows]

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
def _to_state(row: RowMapping) -> MediaItemState:
    return MediaItemState(
        row["media_item_id"],
        domain=row["domain"],
        kind=row["kind"],
        status=ItemStatus(row["status"]),
        version=row["version"],
        updated_at=row["updated_at"],
    )


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
        return _to_state(row) if row is not None else None

    async def get_many(
        self, media_item_ids: Sequence[UUID], *, for_update: bool = False
    ) -> dict[UUID, MediaItemState]:
        """Проекции единиц одним запросом; блокировка строк — по возрастанию id."""
        if not media_item_ids:
            return {}
        c = media_items_state.c
        query = (
            select(media_items_state)
            .where(c.media_item_id.in_(sorted(set(media_item_ids))))
            .order_by(c.media_item_id)
        )
        if for_update:
            query = query.with_for_update()
        rows = (await self._s.execute(query)).mappings().all()
        return {row["media_item_id"]: _to_state(row) for row in rows}

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


# ---------------------------------------------------------------- проекция сборников
class SqlCollectionStateRepository:
    """Версия и статус сборников (ADR 0029)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def advance(self, state: CollectionState) -> bool:
        """``INSERT … ON CONFLICT DO UPDATE WHERE version < excluded.version``: проверка
        версии и запись — один оператор без гонки; строка блокируется до коммита."""
        values = {
            "collection_id": state.id,
            "version": state.version,
            "status": state.status.value,
            "updated_at": state.updated_at,
        }
        stmt = pg_insert(collections_state).values(**values)
        result = await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[collections_state.c.collection_id],
                set_={k: stmt.excluded[k] for k in values if k != "collection_id"},
                where=collections_state.c.version < stmt.excluded.version,
            ).returning(collections_state.c.collection_id)
        )
        return result.first() is not None


# ---------------------------------------------------------------- UGC-изображения
def _to_ugc(row: RowMapping) -> UgcImage:
    return UgcImage(
        row["id"],
        owner_user_id=row["owner_user_id"],
        purpose=row["purpose"],
        status=UgcStatus(row["status"]),
        reason_code=row["reason_code"],
        pending_key=row["pending_key"],
        public_key=row["public_key"],
        width=row["width"],
        height=row["height"],
        size_bytes=row["bytes"],
        sha256=row["sha256"],
        submitted_at=row["submitted_at"],
        decided_at=row["decided_at"],
        moderator_id=row["moderator_id"],
        version=row["version"],
    )


def _ugc_values(image: UgcImage) -> dict[str, Any]:
    return {
        "owner_user_id": image.owner_user_id,
        "purpose": image.purpose,
        "status": image.status.value,
        "reason_code": image.reason_code,
        "pending_key": image.pending_key,
        "public_key": image.public_key,
        "width": image.width,
        "height": image.height,
        "bytes": image.size_bytes,
        "sha256": image.sha256,
        "submitted_at": image.submitted_at,
        "decided_at": image.decided_at,
        "moderator_id": image.moderator_id,
        "version": image.version,
    }


class SqlUgcImageRepository:
    """UGC-изображения: решения сериализуются блокировкой строки (``FOR UPDATE``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, image: UgcImage) -> None:
        """Вставить заявку."""
        await self._s.execute(insert(ugc_images).values(id=image.id, **_ugc_values(image)))

    async def get(self, image_id: UUID, *, for_update: bool = False) -> UgcImage | None:
        """Ассет по id."""
        query = select(ugc_images).where(ugc_images.c.id == image_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        return _to_ugc(row) if row else None

    async def save(self, image: UgcImage) -> None:
        """Записать изменения."""
        await self._s.execute(
            update(ugc_images).where(ugc_images.c.id == image.id).values(**_ugc_values(image))
        )

    async def list_approved_for_owner(
        self, owner_user_id: UUID, purpose: str, *, exclude: UUID
    ) -> list[UgcImage]:
        """Одобренные изображения владельца кроме ``exclude`` (строки блокируются)."""
        c = ugc_images.c
        query = (
            select(ugc_images)
            .where(
                c.owner_user_id == owner_user_id,
                c.purpose == purpose,
                c.status == UgcStatus.APPROVED.value,
                c.id != exclude,
            )
            .order_by(c.submitted_at, c.id)
            .with_for_update()
        )
        return [_to_ugc(r) for r in (await self._s.execute(query)).mappings().all()]

    async def pending_keys(self, keys: Sequence[str]) -> set[str]:
        """Ключи, за которыми есть ``pending``-заявка."""
        if not keys:
            return set()
        c = ugc_images.c
        rows = await self._s.execute(
            select(c.pending_key).where(
                c.status == UgcStatus.PENDING.value, c.pending_key.in_(list(keys))
            )
        )
        return {r[0] for r in rows}

    async def enqueue_purge(self, keys: Sequence[str], now: datetime) -> None:
        """Записать ключи на удаление; повтор ключа не ошибка."""
        if not keys:
            return
        await self._s.execute(
            pg_insert(ugc_object_purge)
            .values([{"key": k, "created_at": now} for k in dict.fromkeys(keys)])
            .on_conflict_do_nothing(index_elements=[ugc_object_purge.c.key])
        )

    async def claim_purge(self, limit: int) -> list[str]:
        """Старые ключи первыми; ``SKIP LOCKED``: параллельные запуски не пересекаются."""
        rows = await self._s.execute(
            select(ugc_object_purge.c.key)
            .order_by(ugc_object_purge.c.created_at, ugc_object_purge.c.key)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [r[0] for r in rows]

    async def forget_purge(self, keys: Sequence[str]) -> None:
        """Стереть строки очереди."""
        if keys:
            await self._s.execute(
                delete(ugc_object_purge).where(ugc_object_purge.c.key.in_(list(keys)))
            )

    async def lock_owner(self, owner_user_id: UUID, purpose: str) -> None:
        """Рекомендательная блокировка транзакции: заявки владельца идут по очереди."""
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"ugc:{owner_user_id}:{purpose}"},
        )

    async def list_pending_for_owner(self, owner_user_id: UUID, purpose: str) -> list[UgcImage]:
        """Ожидающие заявки владельца (строки блокируются)."""
        c = ugc_images.c
        query = (
            select(ugc_images)
            .where(
                c.owner_user_id == owner_user_id,
                c.purpose == purpose,
                c.status == UgcStatus.PENDING.value,
            )
            .order_by(c.submitted_at, c.id)
            .with_for_update()
        )
        return [_to_ugc(r) for r in (await self._s.execute(query)).mappings().all()]

    async def queue(
        self,
        status: UgcStatus,
        purpose: str | None,
        *,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[UgcImage]:
        """Страница очереди по ``(submitted_at, id)``."""
        c = ugc_images.c
        query = select(ugc_images).where(c.status == status.value)
        if purpose is not None:
            query = query.where(c.purpose == purpose)
        if after is not None:
            query = query.where(tuple_(c.submitted_at, c.id) > tuple_(after[0], after[1]))
        query = query.order_by(c.submitted_at, c.id).limit(limit)
        return [_to_ugc(r) for r in (await self._s.execute(query)).mappings().all()]


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
class SqlImportPackageRepository:
    """Пакеты пакетной загрузки: строка пакета и ошибки по файлам."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, package_id: UUID, *, for_update: bool = False) -> ImportPackage | None:
        """Пакет с ошибками (по ``seq``)."""
        query = select(import_packages).where(import_packages.c.id == package_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        if row is None:
            return None
        errors = (
            (
                await self._s.execute(
                    select(import_package_errors)
                    .where(import_package_errors.c.package_id == package_id)
                    .order_by(import_package_errors.c.seq)
                )
            )
            .mappings()
            .all()
        )
        return ImportPackage(
            row["id"],
            actor_id=row["actor_id"],
            reason=row["reason"],
            archive_key=row["archive_key"],
            archive_sha256=row["archive_sha256"],
            archive_bytes=row["archive_bytes"],
            created_at=row["created_at"],
            status=PackageStatus(row["status"]),
            total=row["total"],
            done=row["done"],
            failed=row["failed"],
            missing_source=row["missing_source"],
            missing_license=row["missing_license"],
            errors_dropped=row["errors_dropped"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            heartbeat_at=row["heartbeat_at"],
            errors=[
                PackageFileError(
                    seq=e["seq"],
                    item_key=e["item_key"],
                    path=e["path"],
                    role=e["role"],
                    code=e["code"],
                    message=e["message"],
                )
                for e in errors
            ],
        )

    @staticmethod
    def _values(package: ImportPackage) -> dict[str, Any]:
        return {
            "actor_id": package.actor_id,
            "reason": package.reason,
            "status": package.status.value,
            "archive_key": package.archive_key,
            "archive_sha256": package.archive_sha256,
            "archive_bytes": package.archive_bytes,
            "total": package.total,
            "done": package.done,
            "failed": package.failed,
            "missing_source": package.missing_source,
            "missing_license": package.missing_license,
            "errors_dropped": package.errors_dropped,
            "error_code": package.error_code,
            "error_message": package.error_message,
            "created_at": package.created_at,
            "started_at": package.started_at,
            "finished_at": package.finished_at,
            "heartbeat_at": package.heartbeat_at,
        }

    async def active_archive_keys(self, keys: Sequence[str]) -> set[str]:
        """Ключи архивов, на которые ссылается ещё не завершённый пакет."""
        if not keys:
            return set()
        rows = await self._s.execute(
            select(import_packages.c.archive_key).where(import_packages.c.archive_key.in_(keys))
        )
        return {r[0] for r in rows}

    async def add(self, package: ImportPackage) -> None:
        """Вставить пакет."""
        await self._s.execute(
            insert(import_packages).values(id=package.id, **self._values(package))
        )

    async def save(self, package: ImportPackage) -> None:
        """Обновить пакет и дописать новые ошибки (при перезапуске старые стираются)."""
        await self._s.execute(
            update(import_packages)
            .where(import_packages.c.id == package.id)
            .values(**self._values(package))
        )
        if package.errors_reset:
            await self._s.execute(
                delete(import_package_errors).where(
                    import_package_errors.c.package_id == package.id
                )
            )
            package.errors_reset = False
        if package.new_errors:
            await self._s.execute(
                insert(import_package_errors),
                [
                    {
                        "package_id": package.id,
                        "seq": e.seq,
                        "item_key": e.item_key,
                        "path": e.path,
                        "role": e.role,
                        "code": e.code,
                        "message": e.message,
                    }
                    for e in package.new_errors
                ],
            )
            package.new_errors = []


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
    collections: CollectionStateRepository
    ugc: UgcImageRepository
    packages: ImportPackageRepository
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
        self.collections = SqlCollectionStateRepository(s)
        self.ugc = SqlUgcImageRepository(s)
        self.packages = SqlImportPackageRepository(s)
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
