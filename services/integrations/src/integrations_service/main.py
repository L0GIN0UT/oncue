"""Сборка сервиса: что входит в integrations и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from integrations_service.api import build_routers
from integrations_service.infrastructure.db.tables import TABLES
from integrations_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from integrations_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="integrations",
    settings_cls=Settings,
    description=("Адаптеры внешних провайдеров, шифрованные токены, синхронизация и импорт"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=build_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
