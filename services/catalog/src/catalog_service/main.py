"""Сборка сервиса: что входит в catalog и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from catalog_service.api import build_api_routers
from catalog_service.infrastructure.container import container_for
from catalog_service.infrastructure.db.tables import TABLES
from catalog_service.infrastructure.messaging import (
    CONSUMED_TOPICS,
    build_event_handlers,
    build_task_handlers,
)
from catalog_service.settings import Settings
from gv_service import Infrastructure, ServiceDefinition, main


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры API на контейнере процесса (адаптеры создаются лениво)."""
    container = container_for(infra)
    routers = build_api_routers(container)
    # Клиенты внешних источников и OpenSearch закрываются при остановке приложения.
    routers[0].add_event_handler("shutdown", container.close)
    return routers


DEFINITION = ServiceDefinition(
    name="catalog",
    settings_cls=Settings,
    description=(
        "Медиа-единицы (треки, фильмы, сериалы, аниме), персоны, франшизы, "
        "алиасы, поиск и автокомплит"
    ),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=build_routers,
    build_task_handlers=build_task_handlers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
