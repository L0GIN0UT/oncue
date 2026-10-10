"""Сборка сервиса: что входит в identity и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, main
from identity_service.api import build_routers
from identity_service.infrastructure.db.tables import TABLES
from identity_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from identity_service.settings import Settings
from identity_service.wiring import build_api_deps, build_periodic_jobs


def _routers(infra: Infrastructure) -> list[APIRouter]:
    return build_routers(build_api_deps(infra))


DEFINITION = ServiceDefinition(
    name="identity",
    settings_cls=Settings,
    description=(
        "Аккаунты, вход, сессии и токены, привязка провайдеров, слияние и "
        "удаление аккаунтов, роли и права"
    ),
    tables=TABLES,
    migrations_path=Path(__file__).parent / "infrastructure" / "db" / "migrations",
    build_event_handlers=build_event_handlers,
    consumer_topics=CONSUMED_TOPICS,
    build_routers=_routers,
    build_periodic_jobs=build_periodic_jobs,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
