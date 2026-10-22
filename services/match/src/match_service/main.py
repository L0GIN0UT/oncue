"""Сборка сервиса: что входит в match и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure, PeriodicJob, Runner, ServiceDefinition, main
from match_service.api import build_api_routers
from match_service.infrastructure import jobs, messaging
from match_service.infrastructure.container import MatchAdapters, container_for
from match_service.infrastructure.db.tables import TABLES
from match_service.settings import Settings


def build_routers(infra: Infrastructure, adapters: MatchAdapters | None = None) -> list[APIRouter]:
    """Роутеры (тесты подменяют ``adapters``); контейнер собирается при первом запросе."""
    return build_api_routers(lambda: container_for(infra, adapters))


def build_event_handlers(
    infra: Infrastructure, adapters: MatchAdapters | None = None
) -> EventHandlerRegistry:
    """Обработчики событий identity и game."""
    return messaging.build_event_handlers(container_for(infra, adapters))


def build_periodic_jobs(
    infra: Infrastructure, adapters: MatchAdapters | None = None
) -> list[PeriodicJob]:
    """Периодические задачи (relay): партиции раундов."""
    return jobs.build_jobs(container_for(infra, adapters))


def build_runners(infra: Infrastructure, adapters: MatchAdapters | None = None) -> list[Runner]:
    """Процесс rooms: владельцы комнат (D6, ADR 0034)."""
    return [container_for(infra, adapters).runner()]


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
    consumer_topics=messaging.CONSUMED_TOPICS,
    build_routers=build_routers,
    build_periodic_jobs=build_periodic_jobs,
    build_runners=build_runners,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
