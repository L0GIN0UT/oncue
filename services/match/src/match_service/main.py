"""Сборка сервиса: что входит в match и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from match_service.api import build_routers
from match_service.infrastructure.db.tables import TABLES
from match_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from match_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="match",
    settings_cls=Settings,
    description=(
        "Онлайн: лобби, комнаты, матчмейкинг, конечный автомат матча, таймеры, "
        "реконнект, наблюдение"
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
