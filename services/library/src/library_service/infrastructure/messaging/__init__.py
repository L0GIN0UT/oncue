"""Обработчики событий catalog и identity (план этапа 3b §6, L4).

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
use-cases получают UoW, привязанный к этой сессии (SAVEPOINT). Нарушение контракта payload —
``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.consumer import EventHandler
from gv_messaging.ports import PermanentError
from library_service.domain.library import CatalogEntry, ItemStatus, PersonEntry
from library_service.infrastructure.container import LibraryContainer

CONSUMED_TOPICS: tuple[str, ...] = ("catalog.media_item.v1", "identity.user.v1")

ITEM_CREATED = "catalog.MediaItemCreated"
ITEM_UPDATED = "catalog.MediaItemUpdated"
ITEM_BLOCKED = "catalog.MediaItemBlocked"
ITEM_UNBLOCKED = "catalog.MediaItemUnblocked"
USER_DELETED = "identity.UserDeleted"


class _Payload(BaseModel):
    # Лишние поля контракта (алиасы, метрики, ассеты…) медиатеке не нужны.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Person(_Payload):
    name: str = Field(min_length=1, max_length=500)
    role: str = Field(max_length=32)
    order: int = Field(default=0, ge=0)


class _SnapshotPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)
    domain: Literal["music", "film"]
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=16)
    title: str = Field(min_length=1, max_length=500)
    year: int | None = None
    status: Literal["active", "blocked"]
    persons: list[_Person] = Field(default_factory=list)


class _StatusPayload(_Payload):
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


def build_event_handlers(container: LibraryContainer) -> EventHandlerRegistry:
    """Реестр обработчиков событий."""
    registry = EventHandlerRegistry()

    async def on_snapshot(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_SnapshotPayload, envelope.payload)
        entry = CatalogEntry(
            media_item_id=p.media_item_id,
            domain=p.domain,
            kind=p.kind,
            status=ItemStatus(p.status),
            title=p.title,
            year=p.year,
            persons=tuple(PersonEntry(x.name, x.role, x.order) for x in p.persons),
            version=p.version,
        )
        async with container.uow_factory(session)() as uow:
            await container.catalog_events.snapshot(uow, entry)
            await uow.commit()

    def on_status(status: ItemStatus) -> EventHandler:
        async def handler(envelope: EventEnvelope, session: AsyncSession) -> None:
            p = _parse(_StatusPayload, envelope.payload)
            async with container.uow_factory(session)() as uow:
                await container.catalog_events.status(uow, p.media_item_id, status, p.version)
                await uow.commit()

        return handler

    async def on_user_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserDeletedPayload, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.purge.request(uow, p.user_id)
            await uow.commit()

    registry.register(ITEM_CREATED, on_snapshot)
    registry.register(ITEM_UPDATED, on_snapshot)
    registry.register(ITEM_BLOCKED, on_status(ItemStatus.BLOCKED))
    registry.register(ITEM_UNBLOCKED, on_status(ItemStatus.ACTIVE))
    registry.register(USER_DELETED, on_user_deleted)
    return registry
