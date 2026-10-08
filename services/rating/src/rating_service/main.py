"""Сборка сервиса: что входит в rating и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from rating_service.api import build_routers
from rating_service.infrastructure.db.tables import TABLES
from rating_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from rating_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="rating",
    settings_cls=Settings,
    description=("Эло, калибровка, звания, сезоны, топы недели и сезона, лидерборды"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=build_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
