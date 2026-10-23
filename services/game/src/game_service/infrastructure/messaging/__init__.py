"""Обработчики событий catalog, media-pipeline, collections и identity.

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
use-cases получают UoW, привязанный к этой сессии (SAVEPOINT). Нарушение контракта payload —
``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from game_service.application.commands.projection import AssetsReadyData, CollectionSnapshot
from game_service.domain.projection import (
    CatalogItem,
    CollectionItem,
    CollectionProj,
    CollectionStatus,
    ItemStatus,
    LadderProj,
    catalog_metrics,
)
from game_service.infrastructure.container import GameContainer
from gv_game_kernel import PersonRef
from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.consumer import EventHandler
from gv_messaging.ports import PermanentError

CONSUMED_TOPICS: tuple[str, ...] = (
    "catalog.media_item.v1",
    "media_pipeline.asset_set.v1",
    "identity.user.v1",
    "collections.collection.v1",
    "rating.rating.v1",
)

ITEM_CREATED = "catalog.MediaItemCreated"
ITEM_UPDATED = "catalog.MediaItemUpdated"
ITEM_BLOCKED = "catalog.MediaItemBlocked"
ITEM_UNBLOCKED = "catalog.MediaItemUnblocked"
ASSETS_READY = "media_pipeline.AssetsReady"
ASSETS_FAILED = "media_pipeline.AssetsFailed"
USER_DELETED = "identity.UserDeleted"
COLLECTION_PUBLISHED = "collections.CollectionPublished"
COLLECTION_UPDATED = "collections.CollectionUpdated"
COLLECTION_DELETED = "collections.CollectionDeleted"
LADDER_PUBLISHED = "rating.LadderPublished"
# Максимум единиц сборника (контракт ``collections.collection.v1``, ADR 0028).
MAX_COLLECTION_ITEMS = 200
# Виды алиасов, которые считаются названием единицы (``MediaItemRef.title_aliases``).
TITLE_ALIAS_KINDS = frozenset({"title", "alt"})


class _Payload(BaseModel):
    # Лишние поля контракта (внешние ссылки, метрики…) game не нужны.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Alias(_Payload):
    normalized: str = Field(max_length=500)
    kind: str = Field(max_length=16)


class _Person(_Payload):
    person_id: UUID
    name: str = Field(max_length=500)
    role: str = Field(max_length=32)


class _SnapshotPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)
    domain: Literal["music", "film"]
    kind: Literal["track", "film"]
    title: str = Field(min_length=1, max_length=500)
    year: int | None = None
    status: ItemStatus
    aliases: list[_Alias] = Field(default_factory=list)
    persons: list[_Person] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    # Метрики — словарь чисел (``vote_average``, ``revenue_usd`` …): новые метрики catalog
    # не ломают разбор; нечисловые значения отбрасывает ``catalog_metrics``. Поля этапа 3a
    # (``countries``, ``clue_texts``, ``source_media``) game не нужны и игнорируются.
    # Any: значения проверяет catalog_metrics (числа), остальное отбрасывается.
    metrics: dict[str, Any] | None = None


class _StatusPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)


class _Scope(_Payload):
    kind: Literal["collection"]
    id: UUID


class _ReadyPayload(_Payload):
    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    params_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    levels_count: int = Field(ge=1)
    version: int = Field(ge=1)
    # ADR 0029: набор сборника (свои эмодзи автора); нет или null — каталожный.
    scope: _Scope | None = None


class _Custom(_Payload):
    emoji: str | None = Field(default=None, min_length=1, max_length=128)


class _CollectionItem(_Payload):
    media_item_id: UUID
    position: int = Field(ge=0, lt=MAX_COLLECTION_ITEMS)
    custom: _Custom | None = None


class _CollectionPayload(_Payload):
    # Название, теги, обложка и прочее UGC game не нужны (и не хранятся, D6).
    collection_id: UUID
    version: int = Field(ge=1)
    content_version: int = Field(ge=1)
    domain: Literal["music", "film"]
    status: Literal["published", "hidden"]
    visibility: Literal["public", "unlisted"]
    author_id: UUID | None = None
    items: list[_CollectionItem] = Field(max_length=MAX_COLLECTION_ITEMS)


class _CollectionDeletedPayload(_Payload):
    collection_id: UUID
    version: int = Field(ge=1)
    deleted_at: datetime


class _LadderPayload(_Payload):
    # Окна подбора, форматы очереди и версия конфига нужны match; game — ключ и домен.
    ladder: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    version: int = Field(ge=1)
    domain: Literal["music", "film"]


class _FailedPayload(_Payload):
    asset_set_id: UUID
    media_item_id: UUID
    version: int = Field(ge=1)


class _UserDeletedPayload(_Payload):
    user_id: UUID


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def _popularity(metrics: dict[str, Any] | None) -> float | None:
    value = (metrics or {}).get("popularity")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _collection_snapshot(p: _CollectionPayload, at: datetime) -> CollectionSnapshot:
    """Снимок сборника из payload ``CollectionPublished|Updated`` (UGC-поля не берутся)."""
    snapshot = CollectionSnapshot(
        proj=CollectionProj(
            collection_id=p.collection_id,
            version=p.version,
            content_version=p.content_version,
            domain=p.domain,
            status=CollectionStatus(p.status),
            visibility=p.visibility,
            author_id=p.author_id,
            updated_at=at,
        ),
        items=tuple(
            CollectionItem(
                position=i.position,
                media_item_id=i.media_item_id,
                custom=i.custom is not None and i.custom.emoji is not None,
            )
            for i in p.items
        ),
    )
    if len({i.position for i in snapshot.items}) != len(snapshot.items):
        raise PermanentError("позиции единиц сборника повторяются")
    return snapshot


def build_event_handlers(container: GameContainer) -> EventHandlerRegistry:  # noqa: PLR0915 — реестр: по обработчику на событие
    """Реестр обработчиков событий."""
    registry = EventHandlerRegistry()

    async def on_snapshot(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_SnapshotPayload, envelope.payload)
        item = CatalogItem(
            media_item_id=p.media_item_id,
            domain=p.domain,
            kind=p.kind,
            status=p.status,
            version=p.version,
            title=p.title,
            year=p.year,
            aliases=tuple(dict.fromkeys(a.normalized for a in p.aliases)),
            title_aliases=tuple(
                dict.fromkeys(a.normalized for a in p.aliases if a.kind in TITLE_ALIAS_KINDS)
            ),
            persons=tuple(PersonRef(id=x.person_id, name=x.name, role=x.role) for x in p.persons),
            genres=tuple(p.genres),
            popularity=_popularity(p.metrics),
            metrics=catalog_metrics(p.metrics, year=p.year),
        )
        async with container.uow_factory(session)() as uow:
            await container.catalog_events.snapshot(uow, item)
            await uow.commit()

    def on_status(status: ItemStatus) -> EventHandler:
        async def handler(envelope: EventEnvelope, session: AsyncSession) -> None:
            p = _parse(_StatusPayload, envelope.payload)
            async with container.uow_factory(session)() as uow:
                await container.catalog_events.status(uow, p.media_item_id, status, p.version)
                await uow.commit()

        return handler

    async def on_ready(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_ReadyPayload, envelope.payload)
        data = AssetsReadyData(
            asset_set_id=p.asset_set_id,
            media_item_id=p.media_item_id,
            clue_type=p.clue_type,
            params_hash=p.params_hash,
            levels_count=p.levels_count,
            version=p.version,
            scope_id=p.scope.id if p.scope is not None else None,
        )
        async with container.uow_factory(session)() as uow:
            await container.assets_events.ready(uow, data)
            await uow.commit()

    async def on_collection(envelope: EventEnvelope, session: AsyncSession) -> None:
        snapshot = _collection_snapshot(
            _parse(_CollectionPayload, envelope.payload), envelope.occurred_at
        )
        async with container.uow_factory(session)() as uow:
            await container.collection_events.snapshot(uow, snapshot)
            await uow.commit()

    async def on_collection_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_CollectionDeletedPayload, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.collection_events.deleted(uow, p.collection_id, p.version, p.deleted_at)
            await uow.commit()

    async def on_ladder(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_LadderPayload, envelope.payload)
        proj = LadderProj(
            ladder=p.ladder, domain=p.domain, version=p.version, updated_at=envelope.occurred_at
        )
        async with container.uow_factory(session)() as uow:
            await container.ladder_events.published(uow, proj)
            await uow.commit()

    async def on_failed(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_FailedPayload, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.assets_events.failed(uow, p.media_item_id, p.asset_set_id, p.version)
            await uow.commit()

    async def on_user_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserDeletedPayload, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.forget_user.execute(uow, p.user_id)
            await uow.commit()

    registry.register(ITEM_CREATED, on_snapshot)
    registry.register(ITEM_UPDATED, on_snapshot)
    registry.register(ITEM_BLOCKED, on_status(ItemStatus.BLOCKED))
    registry.register(ITEM_UNBLOCKED, on_status(ItemStatus.ACTIVE))
    registry.register(ASSETS_READY, on_ready)
    registry.register(ASSETS_FAILED, on_failed)
    registry.register(USER_DELETED, on_user_deleted)
    registry.register(COLLECTION_PUBLISHED, on_collection)
    registry.register(COLLECTION_UPDATED, on_collection)
    registry.register(COLLECTION_DELETED, on_collection_deleted)
    registry.register(LADDER_PUBLISHED, on_ladder)
    return registry
