"""HTTP API catalog: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1).

Роутеры собираются в ``catalog_service.main`` (composition root) из ``CatalogServices``.
"""

from __future__ import annotations

from fastapi import APIRouter

from catalog_service.api.admin import build_admin_router
from catalog_service.api.public import build_public_router
from catalog_service.application.services import CatalogServices


def build_api_routers(services: CatalogServices) -> list[APIRouter]:
    """Все роутеры сервиса."""
    return [build_public_router(services), build_admin_router(services)]
