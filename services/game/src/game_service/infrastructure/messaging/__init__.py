"""Обработчики событий catalog, media-pipeline и identity.

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
use-cases получают UoW, привязанный к этой сессии (SAVEPOINT). Нарушение контракта payload —
``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from game_service.application.commands.projection import AssetsReadyData
from game_service.domain.projection import CatalogItem, ItemStatus
from game_service.infrastructure.container import GameContainer
from gv_game_kernel import PersonRef
from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.consumer import EventHandler
from gv_messaging.ports import PermanentError

CONSUMED_TOPICS: tuple[str, ...] = (
    "catalog.media_item.v1",
    "media_pipeline.asset_set.v1",
    "identity.user.v1",
)

ITEM_CREATED = "catalog.MediaItemCreated"
ITEM_UPDATED = "catalog.MediaItemUpdated"
ITEM_BLOCKED = "catalog.MediaItemBlocked"
ITEM_UNBLOCKED = "catalog.MediaItemUnblocked"
ASSETS_READY = "media_pipeline.AssetsReady"
ASSETS_FAILED = "media_pipeline.AssetsFailed"
USER_DELETED = "identity.UserDeleted"
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


class _Metrics(_Payload):
    popularity: float | None = None


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
    metrics: _Metrics | None = None


class _StatusPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)


class _ReadyPayload(_Payload):
    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    params_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    levels_count: int = Field(ge=1)
    version: int = Field(ge=1)


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


def build_event_handlers(container: GameContainer) -> EventHandlerRegistry:
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
            popularity=p.metrics.popularity if p.metrics else None,
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
        )
        async with container.uow_factory(session)() as uow:
            await container.assets_events.ready(uow, data)
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
    return registry
