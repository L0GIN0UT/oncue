"""Сборка сервиса: что входит в progression и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, ServiceSettings, main
from progression_service.api import build_api_routers
from progression_service.infrastructure import jobs
from progression_service.infrastructure.adapters import hmac_key
from progression_service.infrastructure.container import container_for
from progression_service.infrastructure.db.tables import TABLES
from progression_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from progression_service.infrastructure.storage import prepare_storage
from progression_service.settings import Settings


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Роутеры: use-cases контейнера процесса передаются в слой api."""
    c = container_for(infra)
    return build_api_routers(
        infra,
        c.queries,
        c.quest_queries,
        c.badge_queries,
        wheel=c.wheel,
        admin=c.admin,
        shop=c.shop_services,
    )


async def prepare_resources(settings: ServiceSettings) -> None:
    """Бакет косметики и политика публичного префикса - в job ``migrate``."""
    if not isinstance(settings, Settings):  # runner создаёт настройки из settings_cls
        raise TypeError(f"ожидались настройки progression, получено {type(settings)!r}")
    hmac_key(settings)  # вне local/ci без ключа кодов job migrate (а с ним и деплой) падает
    p = settings.progression
    await prepare_storage(settings.s3, bucket=p.cosmetics_bucket, base_url=p.cosmetics_base_url)


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
    prepare_resources=prepare_resources,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
