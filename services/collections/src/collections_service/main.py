"""Сборка сервиса: что входит в collections и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from collections_service.api import build_api_routers
from collections_service.api.common import ApiServices
from collections_service.infrastructure import jobs, messaging
from collections_service.infrastructure.container import CollectionsAdapters, container_for
from collections_service.infrastructure.db.tables import TABLES
from collections_service.settings import Settings
from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure, PeriodicJob, ServiceDefinition, main


def build_routers(
    infra: Infrastructure, adapters: CollectionsAdapters | None = None
) -> list[APIRouter]:
    """Роутеры (тесты подменяют ``adapters``)."""
    c = container_for(infra, adapters)
    return build_api_routers(
        ApiServices(
            service_name=c.settings.service_name,
            author=c.author,
            reactions=c.reactions,
            admin=c.admin,
            queries=c.queries,
            limits=c.settings.collections.limits,
        )
    )


def build_event_handlers(
    infra: Infrastructure, adapters: CollectionsAdapters | None = None
) -> EventHandlerRegistry:
    """Обработчики событий catalog, game, identity."""
    return messaging.build_event_handlers(container_for(infra, adapters))


def build_periodic_jobs(
    infra: Infrastructure, adapters: CollectionsAdapters | None = None
) -> list[PeriodicJob]:
    """Периодические задачи (relay)."""
    return jobs.build_jobs(container_for(infra, adapters))


DEFINITION = ServiceDefinition(
    name="collections",
    settings_cls=Settings,
    description=(
        "Пользовательские и кураторские сборники: конструктор, черновики, каталог, лайки, избранное"
    ),
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
