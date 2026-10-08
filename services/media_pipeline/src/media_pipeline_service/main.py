"""Сборка сервиса: что входит в media_pipeline и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from gv_service import ServiceDefinition, main
from media_pipeline_service.api import build_routers
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from media_pipeline_service.settings import Settings

DEFINITION = ServiceDefinition(
    name="media_pipeline",
    settings_cls=Settings,
    description=(
        "Генерация ассетов загадок по уровням, хранение в S3, подписанные "
        "ссылки, проверка UGC-изображений"
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
