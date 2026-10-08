"""Сборка сервиса: что входит в catalog и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from catalog_service.api import build_routers
from catalog_service.infrastructure.db.tables import TABLES
from catalog_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from catalog_service.settings import Settings
from gv_service import ServiceDefinition, main

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
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
