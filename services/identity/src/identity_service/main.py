"""Сборка сервиса: что входит в identity и как это связано (composition root)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from gv_service import Infrastructure, ServiceDefinition, ServiceSettings, main
from identity_service.api import build_routers
from identity_service.infrastructure.db.tables import TABLES
from identity_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from identity_service.settings import Settings
from identity_service.wiring import (
    build_api_deps,
    build_periodic_jobs,
    ensure_bootstrap_admin,
    reannounce_staff_perms,
)


async def prepare_resources(settings: ServiceSettings) -> None:
    """Подготовка ресурсов командой ``migrate`` (после миграций): bootstrap-админ (local/ci).

    Идемпотентно и безопасно для каждого запуска ``migrate``; вне local/ci с заданными
    учётными данными — ошибка старта (``wiring.bootstrap_admin_credentials``). С флагом
    ``GV_REANNOUNCE_STAFF_PERMS`` повторно публикует права сотрудников (бэкфилл проекций).
    """
    if not isinstance(settings, Settings):  # runner создаёт настройки из settings_cls
        raise TypeError(f"ожидались настройки identity, получено {type(settings)!r}")
    await ensure_bootstrap_admin(settings)
    if settings.reannounce_staff_perms:
        await reannounce_staff_perms(settings)


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
    prepare_resources=prepare_resources,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
