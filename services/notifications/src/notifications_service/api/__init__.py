"""HTTP API notifications: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from gv_service import Infrastructure
from notifications_service.api.admin import build_admin_router
from notifications_service.api.public import build_public_router
from notifications_service.api.services import ApiServices


def build_api_routers(infra: Infrastructure, services: ApiServices) -> list[APIRouter]:
    """Все роутеры сервиса (use-cases приходят из composition root)."""
    return [build_public_router(infra, services), build_admin_router(services)]
