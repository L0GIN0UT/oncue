"""HTTP API collections: публичный (/v1) и админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from collections_service.api.admin import build_admin_router
from collections_service.api.common import ApiServices
from collections_service.api.public import build_public_router


def build_api_routers(services: ApiServices) -> list[APIRouter]:
    """Все роутеры сервиса (use-cases собирает ``main.py``)."""
    return [build_public_router(services), build_admin_router(services)]
