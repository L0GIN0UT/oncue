"""Сборка сервиса: что входит в admin и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from admin_service.api import build_routers
from admin_service.infrastructure.db.tables import TABLES
from admin_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from admin_service.settings import Settings
from gv_service import ServiceDefinition, main

DEFINITION = ServiceDefinition(
    name="admin",
    settings_cls=Settings,
    description=(
        "Админ-панель (Django на этапе 1): модерация, контент, конфиги режимов; сейчас — заглушка"
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
