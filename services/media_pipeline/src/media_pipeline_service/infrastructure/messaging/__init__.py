"""Обработчики событий catalog и задачи RabbitMQ.

Обработчик события работает внутри транзакции ``EventProcessor`` (отметка inbox +
изменения — атомарно); use-cases получают UoW, привязанный к этой сессии (SAVEPOINT).
Устаревшие по ``version`` события use-case отбрасывает сам.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError, TaskHandler
from media_pipeline_service.application.dto import (
    GENERATE_TASK,
    CatalogSource,
    CatalogTexts,
    MediaItemSnapshot,
)
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin
from media_pipeline_service.infrastructure.container import MediaContainer

log = structlog.get_logger(__name__)

CONSUMED_TOPICS: tuple[str, ...] = ("catalog.media_item.v1",)

ITEM_CREATED = "catalog.MediaItemCreated"
ITEM_UPDATED = "catalog.MediaItemUpdated"
ITEM_BLOCKED = "catalog.MediaItemBlocked"
ITEM_UNBLOCKED = "catalog.MediaItemUnblocked"


class _Payload(BaseModel):
    # Лишние поля контракта (алиасы, персоны…) сервису не нужны — игнорируем.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _SourceMediaPayload(_Payload):
    source_media_ref: UUID
    # Строка, а не SourceKind: catalog добавляет виды раньше, чем их начинает обрабатывать
    # media-pipeline (план этапа 3a, M2) — неизвестный вид пропускается, а не роняет событие.
    kind: str = Field(min_length=1, max_length=32)
    origin: SourceOrigin
    uri: str = Field(min_length=1, max_length=2000)
    position: int = Field(default=1, ge=1, le=100)
    source_url: str | None = Field(default=None, max_length=2000)
    license_note: str | None = Field(default=None, max_length=500)


class _PersonPayload(_Payload):
    name: str = Field(min_length=1, max_length=200)
    # Строка: новые роли catalog не должны ронять событие (в текст идут director и actor).
    role: str = Field(min_length=1, max_length=32)
    order: int = Field(ge=0)


class _ClueTextsPayload(_Payload):
    plot_masked: str | None = Field(default=None, max_length=2000)
    tagline: str | None = Field(default=None, max_length=300)
    emoji: str | None = Field(default=None, max_length=64)


class _SnapshotPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)
    domain: Literal["music", "film"]
    kind: Literal["track", "film"]
    status: ItemStatus
    source_media: list[_SourceMediaPayload]
    # Этап 3a (ADR 0024): необязательные поля — старые события их не содержат.
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    persons: list[_PersonPayload] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list, max_length=20)
    clue_texts: _ClueTextsPayload | None = None

    def texts(self) -> CatalogTexts:
        """Тексты и метаданные для текстового исходника (без названия единицы)."""
        by_order = sorted(self.persons, key=lambda p: p.order)
        clue = self.clue_texts.model_dump(exclude_none=True) if self.clue_texts else {}
        return CatalogTexts(
            clue_texts=clue,
            year=self.year,
            genres=tuple(self.genres),
            countries=tuple(self.countries),
            directors=tuple(p.name for p in by_order if p.role == "director"),
            cast=tuple(p.name for p in by_order if p.role == "actor"),
        )


class _StatusPayload(_Payload):
    media_item_id: UUID
    version: int = Field(ge=1)


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        # Нарушен контракт — повтор не поможет, сообщение уходит в DLQ.
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def known_sources(items: Sequence[_SourceMediaPayload]) -> tuple[CatalogSource, ...]:
    """Исходники известных сервису видов; неизвестные пропускаются с записью в лог."""
    known = {k.value for k in SourceKind}
    sources: list[CatalogSource] = []
    for s in items:
        if s.kind not in known:
            log.info(
                "media.source_kind_skipped", kind=s.kind, source_media_ref=str(s.source_media_ref)
            )
            continue
        sources.append(
            CatalogSource(
                s.source_media_ref,
                SourceKind(s.kind),
                s.origin,
                s.uri,
                position=s.position,
                source_url=s.source_url,
                license_note=s.license_note,
            )
        )
    return tuple(sources)


def build_event_handlers(container: MediaContainer) -> EventHandlerRegistry:
    """Реестр обработчиков событий catalog."""
    registry = EventHandlerRegistry()

    async def on_snapshot(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_SnapshotPayload, envelope.payload)
        snapshot = MediaItemSnapshot(
            media_item_id=p.media_item_id,
            version=p.version,
            domain=p.domain,
            kind=p.kind,
            status=p.status,
            sources=known_sources(p.source_media),
            texts=p.texts(),
        )
        await container.sync_media_item(container.uow_factory(session)).apply_snapshot(snapshot)

    def on_status(status: ItemStatus) -> Any:
        async def handler(envelope: EventEnvelope, session: AsyncSession) -> None:
            p = _parse(_StatusPayload, envelope.payload)
            use_case = container.sync_media_item(container.uow_factory(session))
            await use_case.apply_status(p.media_item_id, p.version, status)

        return handler

    registry.register(ITEM_CREATED, on_snapshot)
    registry.register(ITEM_UPDATED, on_snapshot)
    registry.register(ITEM_BLOCKED, on_status(ItemStatus.BLOCKED))
    registry.register(ITEM_UNBLOCKED, on_status(ItemStatus.ACTIVE))
    return registry


class _GeneratePayload(_Payload):
    asset_set_id: UUID
    generation: int = Field(ge=1)


def build_task_handlers(container: MediaContainer) -> dict[str, TaskHandler]:
    """Обработчики задач RabbitMQ.

    ``TaskNotVisibleError`` и ``GenerationRetryError`` пробрасываются: очередь повторит
    задачу через delay-очередь (``gv_messaging.rabbit``).
    """

    async def generate(payload: dict[str, Any]) -> None:
        p = _parse(_GeneratePayload, payload)
        await container.generate.execute(p.asset_set_id, p.generation)

    return {GENERATE_TASK: generate}
