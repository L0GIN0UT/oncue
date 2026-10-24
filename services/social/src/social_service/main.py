"""Сборка сервиса: что входит в social и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, main
from social_service.api import build_routers
from social_service.infrastructure import jobs
from social_service.infrastructure.container import container_for
from social_service.infrastructure.db.tables import TABLES
from social_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from social_service.settings import Settings


def routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры: use-cases контейнера процесса передаются в слой api."""
    return build_routers(infra, lambda: container_for(infra).friends)


DEFINITION = ServiceDefinition(
    name="social",
    settings_cls=Settings,
    description=("Друзья, комментарии, жалобы, модерация, шкалы поведения и санкции, приватность"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=routers,
    build_periodic_jobs=jobs.build_jobs,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
