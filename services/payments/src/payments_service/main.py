"""Сборка сервиса: что входит в payments и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from payments_service.api import build_routers
from payments_service.infrastructure.db.tables import TABLES
from payments_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from payments_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="payments",
    settings_cls=Settings,
    description=(
        "Тарифы и подписки, платёжный провайдер, вебхуки, entitlements. "
        "Выключен флагом billing.enabled"
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
