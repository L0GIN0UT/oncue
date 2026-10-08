"""Сборка сервиса: что входит в collections и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from collections_service.api import build_routers
from collections_service.infrastructure.db.tables import TABLES
from collections_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from collections_service.settings import Settings
from gv_service import ServiceDefinition, main

DEFINITION = ServiceDefinition(
    name="collections",
    settings_cls=Settings,
    description=(
        "Пользовательские и кураторские сборники: конструктор, черновики, каталог, лайки, избранное"
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
