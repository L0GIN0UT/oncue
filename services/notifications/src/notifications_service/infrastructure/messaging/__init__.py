"""Обработчики событий progression и media-pipeline и задача рассылки (ADR 0047).

Какие события слушаем и что из них делаем, решает конфиг типов: обработчики регистрируются по
``source_event`` каждого типа, а не по списку в коде. Событие без типа в конфиге пропускается
(``EventProcessor`` не находит обработчика); событие, не подходящее под контракт типа, -
``PermanentError`` (сразу в DLQ). Notifications ничего не публикует.

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox и запись уведомления
атомарны); публикация в Centrifugo идёт после её коммита.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from prometheus_client import Counter
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError, TaskHandler
from gv_service import Infrastructure
from notifications_service.application.broadcasts import BROADCAST_TASK
from notifications_service.domain.errors import MappingError
from notifications_service.infrastructure.container import container_for

log = structlog.get_logger(__name__)

CONSUMED_TOPICS: tuple[str, ...] = (
    "progression.progression.v1",
    "media_pipeline.ugc_image.v1",
)

NOTIFICATIONS_CREATED = Counter("gv_notifications_created_total", "Созданные уведомления", ["type"])


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков: по одному на каждое событие-источник из конфига типов."""
    container = container_for(infra)
    registry = EventHandlerRegistry()

    async def handle(envelope: EventEnvelope, session: AsyncSession) -> None:
        try:
            created = await container.ingest(session).execute(envelope.event_type, envelope.payload)
        except MappingError as exc:
            # Битое событие повтором не лечится.
            raise PermanentError(f"событие не соответствует контракту типа: {exc}") from exc
        for notification in created:
            NOTIFICATIONS_CREATED.labels(notification.type_key).inc()
        container.dispatcher.schedule(session, created)

    for event_type in sorted(container.catalog.source_events):
        registry.register(event_type, handle)
    return registry


def build_task_handlers(infra: Infrastructure) -> dict[str, TaskHandler]:
    """Задачи RabbitMQ: отправка рассылки.

    ``BroadcastNotVisibleError`` и ``RuntimeError`` (часть писем не ушла) пробрасываются: очередь
    повторит задачу по задержкам; ``PermanentError`` - сразу в DLQ.
    """
    container = container_for(infra)

    async def send(payload: dict[str, Any]) -> None:
        try:
            broadcast_id = UUID(str(payload["broadcast_id"]))
        except (KeyError, ValueError) as exc:
            raise PermanentError("в задаче нет корректного broadcast_id") from exc
        await container.send_broadcast.execute(broadcast_id)

    return {BROADCAST_TASK: send}


__all__ = ["CONSUMED_TOPICS", "build_event_handlers", "build_task_handlers"]
