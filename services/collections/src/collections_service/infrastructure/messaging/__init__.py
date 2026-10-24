"""Обработчики событий catalog, game, identity и social (K7, 5c).

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
use-cases получают UoW, привязанный к этой сессии (SAVEPOINT), и свои события пишут в
outbox той же транзакции. Нарушение контракта payload — ``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from collections_service.application.commands.projection import GameFinishedData
from collections_service.application.ports import CatalogItemSnapshot
from collections_service.infrastructure.container import CollectionsContainer
from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.consumer import EventHandler
from gv_messaging.ports import PermanentError

CONSUMED_TOPICS: tuple[str, ...] = (
    "catalog.media_item.v1",
    "game.session.v1",
    "identity.user.v1",
    "social.social.v1",
)

ITEM_CREATED = "catalog.MediaItemCreated"
ITEM_UPDATED = "catalog.MediaItemUpdated"
ITEM_BLOCKED = "catalog.MediaItemBlocked"
ITEM_UNBLOCKED = "catalog.MediaItemUnblocked"
GAME_FINISHED = "game.GameFinished"
USER_REGISTERED = "identity.UserRegistered"
USER_PROFILE_CHANGED = "identity.UserProfileBasicsChanged"
USER_DELETED = "identity.UserDeleted"
CONTENT_MODERATED = "social.ContentModerated"


class _Payload(BaseModel):
    # Лишние поля контракта (алиасы, персоны, раунды…) collections не нужны.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _ItemPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)
    domain: Literal["music", "film"]
    title: str = Field(min_length=1, max_length=500)
    year: int | None = None
    status: Literal["active", "blocked"]


class _StatusPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)


class _GamePayload(_Payload):
    session_id: UUID
    user_id: UUID | None
    is_guest: bool
    score: int = Field(ge=0)
    max_score: int = Field(ge=0)
    finished_at: datetime
    collection_id: UUID | None = None
    collection_version: int | None = Field(default=None, ge=1)


class _UserPayload(_Payload):
    user_id: UUID
    nickname: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class _ModeratedPayload(_Payload):
    case_id: UUID
    decision: str
    moderator_id: UUID
    target_id: UUID
    target_kind: str
    target_user_id: UUID


class _UserDeletedPayload(_Payload):
    user_id: UUID
    version: int = Field(ge=1)


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def build_event_handlers(container: CollectionsContainer) -> EventHandlerRegistry:
    """Реестр обработчиков событий."""
    registry = EventHandlerRegistry()

    async def on_item(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_ItemPayload, envelope.payload)
        item = CatalogItemSnapshot(
            media_item_id=p.media_item_id,
            domain=p.domain,
            status=p.status,
            version=p.version,
            title=p.title,
            year=p.year,
        )
        async with container.uow_factory(session)() as uow:
            await container.catalog_events.snapshot(uow, item)
            await uow.commit()

    def on_status(status: str) -> EventHandler:
        async def handler(envelope: EventEnvelope, session: AsyncSession) -> None:
            p = _parse(_StatusPayload, envelope.payload)
            async with container.uow_factory(session)() as uow:
                await container.catalog_events.status(uow, p.media_item_id, status, p.version)
                await uow.commit()

        return handler

    async def on_game_finished(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_GamePayload, envelope.payload)
        if p.collection_id is None:
            return  # не сборник: таких сессий большинство, разбор дальше не нужен
        data = GameFinishedData(
            session_id=p.session_id,
            user_id=p.user_id,
            is_guest=p.is_guest,
            collection_id=p.collection_id,
            collection_version=p.collection_version,
            score=p.score,
            max_score=p.max_score,
            finished_at=p.finished_at,
        )
        async with container.uow_factory(session)() as uow:
            await container.completions.apply(uow, data)
            await uow.commit()

    async def on_user(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserPayload, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.users.profile(uow, p.user_id, p.nickname, p.version)
            await uow.commit()

    async def on_user_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserDeletedPayload, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.users.deleted(uow, p.user_id, p.version)
            await uow.commit()

    async def on_content_moderated(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_ModeratedPayload, envelope.payload)
        if p.decision != "content_removed" or p.target_kind != "collection":
            return  # остальные решения (комментарии, аватарки, санкции) сборников не касаются
        async with container.uow_factory(session)() as uow:
            await container.moderation.content_removed(
                uow,
                p.target_id,
                case_id=p.case_id,
                moderator_id=p.moderator_id,
                target_user_id=p.target_user_id,
            )
            await uow.commit()

    registry.register(CONTENT_MODERATED, on_content_moderated)
    registry.register(ITEM_CREATED, on_item)
    registry.register(ITEM_UPDATED, on_item)
    registry.register(ITEM_BLOCKED, on_status("blocked"))
    registry.register(ITEM_UNBLOCKED, on_status("active"))
    registry.register(GAME_FINISHED, on_game_finished)
    registry.register(USER_REGISTERED, on_user)
    registry.register(USER_PROFILE_CHANGED, on_user)
    registry.register(USER_DELETED, on_user_deleted)
    return registry
