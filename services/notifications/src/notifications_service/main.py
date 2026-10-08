"""Сборка сервиса: что входит в notifications и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from notifications_service.api import build_routers
from notifications_service.infrastructure.db.tables import TABLES
from notifications_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from notifications_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="notifications",
    settings_cls=Settings,
    description=("In-app уведомления, realtime-доставка, e-mail, web-push, отключение по типам"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=build_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
