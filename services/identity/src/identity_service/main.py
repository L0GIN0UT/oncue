"""Сборка сервиса: что входит в identity и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from identity_service.api import build_routers
from identity_service.infrastructure.db.tables import TABLES
from identity_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from identity_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="identity",
    settings_cls=Settings,
    description=(
        "Аккаунты, вход, сессии и токены, привязка провайдеров, слияние и "
        "удаление аккаунтов, роли и права"
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
