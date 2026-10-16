"""Сборка сервиса: что входит в library и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure, PeriodicJob, ServiceDefinition, main
from library_service.api import build_api_routers
from library_service.api.common import ApiServices
from library_service.infrastructure import jobs, messaging
from library_service.infrastructure.container import LibraryAdapters, container_for
from library_service.infrastructure.db.tables import TABLES
from library_service.settings import Settings


def build_routers(
    infra: Infrastructure, adapters: LibraryAdapters | None = None
) -> list[APIRouter]:
    """Роутеры (тесты подменяют ``adapters``)."""
    c = container_for(infra, adapters)
    return build_api_routers(
        ApiServices(service_name=c.settings.service_name, commands=c.commands, queries=c.queries)
    )


def build_event_handlers(
    infra: Infrastructure, adapters: LibraryAdapters | None = None
) -> EventHandlerRegistry:
    """Обработчики событий catalog и identity."""
    return messaging.build_event_handlers(container_for(infra, adapters))


def build_periodic_jobs(
    infra: Infrastructure, adapters: LibraryAdapters | None = None
) -> list[PeriodicJob]:
    """Периодические задачи (relay)."""
    return jobs.build_jobs(container_for(infra, adapters))


DEFINITION = ServiceDefinition(
    name="library",
    settings_cls=Settings,
    description=("Личная медиатека: плейлисты, «смотреть позже», «просмотрено», «любимое»"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=messaging.CONSUMED_TOPICS,
    build_routers=build_routers,
    build_periodic_jobs=build_periodic_jobs,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
