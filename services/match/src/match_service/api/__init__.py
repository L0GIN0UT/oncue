"""HTTP API match: публичный (/v1) и админский (/admin/v1); internal-ручек нет."""

from __future__ import annotations

from fastapi import APIRouter

from match_service.api.admin import build_admin_router
from match_service.api.common import ServicesProvider
from match_service.api.public import build_public_router


def build_api_routers(services: ServicesProvider) -> list[APIRouter]:
    """Все роутеры сервиса (use-cases — лениво через ``services``)."""
    return [build_public_router(services), build_admin_router(services)]
