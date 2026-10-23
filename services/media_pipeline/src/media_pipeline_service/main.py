"""Сборка сервиса: что входит в media_pipeline и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_messaging import EventHandlerRegistry
from gv_messaging.ports import TaskHandler
from gv_service import Infrastructure, PeriodicJob, ServiceDefinition, ServiceSettings, main
from media_pipeline_service.api.admin import build_admin_router
from media_pipeline_service.api.internal import build_internal_router
from media_pipeline_service.api.public import build_public_router
from media_pipeline_service.infrastructure import jobs, messaging
from media_pipeline_service.infrastructure.container import MediaAdapters, container_for
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.infrastructure.storage import prepare_storage
from media_pipeline_service.settings import Settings

_MB = 1024 * 1024


def build_routers(infra: Infrastructure, adapters: MediaAdapters | None = None) -> list[APIRouter]:
    """Все роутеры сервиса (DI через ``infra``; тесты подменяют ``adapters``)."""
    c = container_for(infra, adapters)
    # Ленивое соединение RabbitMQ закрывает Infrastructure.close (container_for → add_closer).
    return [
        build_public_router(infra),
        build_internal_router(c.issue_link, c.list_sets, c.ugc),
        build_admin_router(
            upload=c.upload,
            set_position=c.set_position,
            regenerate=c.regenerate,
            list_sets=c.list_sets,
            list_profiles=c.list_profiles,
            list_sources=c.list_sources,
            ugc=c.ugc,
            max_upload_bytes=c.settings.media.max_upload_mb * _MB,
        ),
    ]


def build_event_handlers(
    infra: Infrastructure, adapters: MediaAdapters | None = None
) -> EventHandlerRegistry:
    """Обработчики событий catalog, game и collections."""
    return messaging.build_event_handlers(container_for(infra, adapters))


def build_task_handlers(
    infra: Infrastructure, adapters: MediaAdapters | None = None
) -> dict[str, TaskHandler]:
    """Обработчики задач RabbitMQ."""
    return messaging.build_task_handlers(container_for(infra, adapters))


def build_periodic_jobs(
    infra: Infrastructure, adapters: MediaAdapters | None = None
) -> list[PeriodicJob]:
    """Периодические задачи (relay)."""
    return jobs.build_jobs(container_for(infra, adapters))


async def prepare_resources(settings: ServiceSettings) -> None:
    """Бакеты и политика бакета ассетов — в job ``migrate`` до старта процессов сервиса."""
    if not isinstance(settings, Settings):  # runner создаёт настройки из settings_cls
        raise TypeError(f"ожидались настройки media_pipeline, получено {type(settings)!r}")
    await prepare_storage(settings.s3)


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
    consumer_topics=messaging.CONSUMED_TOPICS,
    build_routers=build_routers,
    build_task_handlers=build_task_handlers,
    build_periodic_jobs=build_periodic_jobs,
    prepare_resources=prepare_resources,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
