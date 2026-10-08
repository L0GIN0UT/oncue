"""Сборка сервиса: что входит в progression и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from progression_service.api import build_routers
from progression_service.infrastructure.db.tables import TABLES
from progression_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from progression_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="progression",
    settings_cls=Settings,
    description=(
        "Опыт и уровни, валюта (журнал проводок), задания, бейджи, магазин, "
        "инвентарь, оформление профиля"
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
