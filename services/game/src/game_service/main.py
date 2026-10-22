"""Сборка сервиса: что входит в game и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from game_service.api import build_api_routers
from game_service.api.common import ApiServices
from game_service.infrastructure import jobs, messaging
from game_service.infrastructure.container import GameAdapters, container_for
from game_service.infrastructure.db.tables import TABLES
from game_service.settings import Settings
from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure, PeriodicJob, ServiceDefinition, main


def build_routers(infra: Infrastructure, adapters: GameAdapters | None = None) -> list[APIRouter]:
    """Роутеры (тесты подменяют ``adapters``)."""
    c = container_for(infra, adapters)
    return build_api_routers(
        ApiServices(
            service_name=c.settings.service_name,
            play=c.play,
            queries=c.queries,
            mode_commands=c.mode_commands,
            mode_queries=c.mode_queries,
            daily_admin=c.daily_admin,
            asset_requests=c.asset_requests,
            round_plans=c.round_plans,
            editor_schema=c.adapters.registry.editor_schema,
        )
    )


def build_event_handlers(
    infra: Infrastructure, adapters: GameAdapters | None = None
) -> EventHandlerRegistry:
    """Обработчики событий catalog, media-pipeline, identity."""
    return messaging.build_event_handlers(container_for(infra, adapters))


def build_periodic_jobs(
    infra: Infrastructure, adapters: GameAdapters | None = None
) -> list[PeriodicJob]:
    """Периодические задачи (relay)."""
    return jobs.build_jobs(container_for(infra, adapters))


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
    consumer_topics=messaging.CONSUMED_TOPICS,
    build_routers=build_routers,
    build_periodic_jobs=build_periodic_jobs,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
