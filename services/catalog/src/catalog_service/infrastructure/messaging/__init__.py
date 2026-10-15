"""Обработчики событий и фоновых задач catalog.

* ``catalog.media_item.v1`` (свой топик) → индексация в OpenSearch. Inbox (через
  ``EventProcessor``) отсекает дубли, ``version_type=external`` — устаревшие события.
* ``media_pipeline.asset_set.v1`` → проекция готовности ассетов загадок (``MarkClueAssets``),
  устаревшие по ``version`` события отбрасываются.
* RabbitMQ-задача ``catalog.import`` → ``RunImport`` (идемпотентна по ``import_id``);
* RabbitMQ-задача ``republish`` → ``RunRepublishBatch`` (порция; повтор — no-op по курсору).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_service.application.commands.imports import IMPORT_TASK
from catalog_service.application.commands.projections import (
    ClueAssetsEvent,
    IndexItem,
    MarkClueAssets,
)
from catalog_service.application.commands.republish import REPUBLISH_TASK
from catalog_service.infrastructure.container import container_for
from catalog_service.infrastructure.db.repositories import (
    SqlClueStatusRepository,
    SqlMediaItemRepository,
)
from gv_messaging import EventEnvelope, EventHandlerRegistry, PermanentError
from gv_messaging.ports import TaskHandler
from gv_service import Infrastructure

log = structlog.get_logger(__name__)

OWN_TOPIC = "catalog.media_item.v1"
MEDIA_PIPELINE_TOPIC = "media_pipeline.asset_set.v1"
CONSUMED_TOPICS: tuple[str, ...] = (OWN_TOPIC, MEDIA_PIPELINE_TOPIC)

CATALOG_EVENTS = (
    "catalog.MediaItemCreated",
    "catalog.MediaItemUpdated",
    "catalog.MediaItemBlocked",
    "catalog.MediaItemUnblocked",
)


def _uuid(payload: dict[str, Any], name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[name]))
    except (KeyError, ValueError) as exc:
        raise PermanentError(f"в payload нет корректного {name}") from exc


def _int(payload: dict[str, Any], name: str, *, optional: bool = False) -> int | None:
    value = payload.get(name)
    if value is None and optional:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PermanentError(f"в payload нет корректного {name}")
    return value


def _str(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value or len(value) > 64:  # noqa: PLR2004
        raise PermanentError(f"в payload нет корректного {name}")
    return value


def parse_clue_event(envelope: EventEnvelope) -> ClueAssetsEvent:
    """Payload ``AssetsReady``/``AssetsFailed`` → событие проекции (битое — ``PermanentError``)."""
    p = envelope.payload
    ready = envelope.event_type.endswith("AssetsReady")
    version = _int(p, "version")
    assert version is not None  # noqa: S101 — _int без optional не возвращает None
    return ClueAssetsEvent(
        media_item_id=_uuid(p, "media_item_id"),
        asset_set_id=_uuid(p, "asset_set_id"),
        clue_type=_str(p, "clue_type"),
        profile_key=_str(p, "profile_key"),
        status="ready" if ready else "failed",
        version=version,
        levels_count=_int(p, "levels_count", optional=True) if ready else None,
        error_code=None if ready else _str(p, "error_code"),
    )


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков событий сервиса."""
    c = container_for(infra)
    registry = EventHandlerRegistry()

    async def index_item(envelope: EventEnvelope, session: AsyncSession) -> None:
        version = _int(envelope.payload, "version")
        assert version is not None  # noqa: S101
        await IndexItem(SqlMediaItemRepository(session), c.search_index, c.cache)(
            _uuid(envelope.payload, "media_item_id"), version
        )

    async def mark_clue_assets(envelope: EventEnvelope, session: AsyncSession) -> None:
        await MarkClueAssets(SqlMediaItemRepository(session), SqlClueStatusRepository(session))(
            parse_clue_event(envelope)
        )

    for event_type in CATALOG_EVENTS:
        registry.register(event_type, index_item)
    registry.register("media_pipeline.AssetsReady", mark_clue_assets)
    registry.register("media_pipeline.AssetsFailed", mark_clue_assets)
    return registry


def build_task_handlers(infra: Infrastructure) -> dict[str, TaskHandler]:
    """Обработчики RabbitMQ-задач: ``import`` и ``republish``."""
    c = container_for(infra)

    async def run_import(payload: dict[str, Any]) -> None:
        await c.run_import(_uuid(payload, "import_id"))

    async def run_republish(payload: dict[str, Any]) -> None:
        after = _uuid(payload, "after") if payload.get("after") is not None else None
        await c.run_republish(_uuid(payload, "job_id"), after)

    return {IMPORT_TASK: run_import, REPUBLISH_TASK: run_republish}
