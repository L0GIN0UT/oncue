"""HTTP API rating: публичный (/v1) и админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from rating_service.api.admin import build_admin_router
from rating_service.api.common import ApiServices
from rating_service.api.public import build_public_router


def build_api_routers(services: ApiServices) -> list[APIRouter]:
    """Все роутеры сервиса."""
    return [
        build_public_router(services.service_name, services.queries),
        build_admin_router(
            services.ladder_commands,
            services.ladder_queries,
            services.season_commands,
            services.rebuild,
        ),
    ]
