"""Сборка сервиса: что входит в notifications и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_messaging import EventHandlerRegistry
from gv_messaging.ports import TaskHandler
from gv_service import Infrastructure, ServiceDefinition, main
from notifications_service.api import build_api_routers
from notifications_service.api.services import ApiServices
from notifications_service.infrastructure import jobs, messaging
from notifications_service.infrastructure.container import container_for
from notifications_service.infrastructure.db.tables import TABLES
from notifications_service.settings import Settings


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры: use-cases контейнера процесса передаются в слой api."""
    c = container_for(infra)
    services = ApiServices(
        queries=c.queries,
        preferences=c.preferences,
        devices=c.devices,
        unsubscribe=c.unsubscribe,
        create_broadcast=c.create_broadcast,
        broadcast_queries=c.broadcast_queries,
        type_admin=c.type_admin,
    )
    return build_api_routers(infra, services)


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Обработчики событий progression и media-pipeline по конфигу типов."""
    return messaging.build_event_handlers(infra)


def build_task_handlers(infra: Infrastructure) -> dict[str, TaskHandler]:
    """Обработчики задач RabbitMQ (рассылка)."""
    return messaging.build_task_handlers(infra)


DEFINITION = ServiceDefinition(
    name="notifications",
    settings_cls=Settings,
    description=("In-app уведомления, realtime-доставка, e-mail, web-push, отключение по типам"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=messaging.CONSUMED_TOPICS,
    build_routers=build_routers,
    build_task_handlers=build_task_handlers,
    build_periodic_jobs=jobs.build_jobs,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
