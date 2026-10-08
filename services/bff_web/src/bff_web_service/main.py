"""Сборка сервиса: что входит в bff_web и как это связано (composition root)."""

from __future__ import annotations

from bff_web_service.api import build_routers
from bff_web_service.settings import Settings
from gv_service import ServiceDefinition, main

DEFINITION = ServiceDefinition(
    name="bff_web",
    settings_cls=Settings,
    description=("Backend-for-frontend: агрегация данных под экраны веб-клиента"),
    build_routers=build_routers,
)


def run() -> None:
    """Консольная точка входа."""
    main(DEFINITION)
