"""Сборка сервиса: что входит в bff_web и как это связано (composition root)."""

from __future__ import annotations

from fastapi import APIRouter

from bff_web_service.api import build_routers
from bff_web_service.settings import Settings
from bff_web_service.wiring import build_deps
from gv_service import Infrastructure, ServiceDefinition, main


def _routers(infra: Infrastructure) -> list[APIRouter]:
    return build_routers(build_deps(infra))


DEFINITION = ServiceDefinition(
    name="bff_web",
    settings_cls=Settings,
    description=("Backend-for-frontend: агрегация данных под экраны веб-клиента"),
    build_routers=_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
