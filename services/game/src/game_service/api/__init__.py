"""HTTP API game: публичный (/v1), внутренний (/internal/v1) и админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from game_service.api.admin import build_admin_router
from game_service.api.common import ApiServices
from game_service.api.internal import build_internal_router
from game_service.api.public import build_public_router


def build_api_routers(services: ApiServices) -> list[APIRouter]:
    """Все роутеры сервиса."""
    return [
        build_public_router(services),
        build_internal_router(services),
        build_admin_router(services),
    ]
