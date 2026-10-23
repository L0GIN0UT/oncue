"""Сборка сервиса: что входит в rating и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, main
from rating_service.api import build_api_routers
from rating_service.api.common import ApiServices
from rating_service.application.commands.rebuild import RebuildLeaderboard
from rating_service.infrastructure import jobs
from rating_service.infrastructure.container import container_for
from rating_service.infrastructure.db.tables import TABLES
from rating_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from rating_service.settings import Settings


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры: use-cases контейнера процесса передаются в слой api."""
    c = container_for(infra)
    return build_api_routers(
        ApiServices(
            service_name=c.settings.service_name,
            queries=c.queries,
            ladder_commands=c.ladder_commands,
            ladder_queries=c.ladder_queries,
            season_commands=c.season_commands,
            rebuild=RebuildLeaderboard(c.uow_factory(), infra.clock),
        )
    )


DEFINITION = ServiceDefinition(
    name="rating",
    settings_cls=Settings,
    description=("Эло, калибровка, звания, сезоны, топы недели и сезона, лидерборды"),
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
