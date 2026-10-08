"""Сборка сервиса: что входит в library и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from library_service.api import build_routers
from library_service.infrastructure.db.tables import TABLES
from library_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from library_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="library",
    settings_cls=Settings,
    description=("Личная медиатека: плейлисты, «смотреть позже», «просмотрено», «любимое»"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=build_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
