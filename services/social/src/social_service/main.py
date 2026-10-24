"""Сборка сервиса: что входит в social и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, main
from social_service.api import build_routers as build_api_routers
from social_service.api.discussion_services import DiscussionServices
from social_service.infrastructure.db.tables import TABLES
from social_service.infrastructure.discussion_container import container_for
from social_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from social_service.settings import Settings


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры: use-cases контейнера процесса передаются в слой api."""
    container = container_for(infra)
    return build_api_routers(
        infra, DiscussionServices(comments=container.comments, reports=container.reports())
    )


DEFINITION = ServiceDefinition(
    name="social",
    settings_cls=Settings,
    description=("Друзья, комментарии, жалобы, модерация, шкалы поведения и санкции, приватность"),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=build_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
