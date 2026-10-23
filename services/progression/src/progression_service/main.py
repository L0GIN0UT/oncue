"""Сборка сервиса: что входит в progression и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, main
from progression_service.api import build_api_routers
from progression_service.infrastructure import jobs
from progression_service.infrastructure.container import container_for
from progression_service.infrastructure.db.tables import TABLES
from progression_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from progression_service.settings import Settings


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры: use-cases контейнера процесса передаются в слой api."""
    container = container_for(infra)
    return build_api_routers(infra, container.queries, container.admin)


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
    build_periodic_jobs=jobs.build_jobs,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
