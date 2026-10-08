"""Сборка сервиса: что входит в game и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from game_service.api import build_routers
from game_service.infrastructure.db.tables import TABLES
from game_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from game_service.settings import Settings
from gv_service import ServiceDefinition, main

DEFINITION = ServiceDefinition(
    name="game",
    settings_cls=Settings,
    description=(
        "Соло-движок: сессии, раунды, ответы, очки; реестр плагинов загадок; "
        "конфиги режимов; дейлик, больше/меньше, топ-32"
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
