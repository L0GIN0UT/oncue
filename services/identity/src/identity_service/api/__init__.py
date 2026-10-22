"""HTTP API identity: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from identity_service.api.admin import build_admin_router
from identity_service.api.common import ApiDeps
from identity_service.api.internal import build_internal_router
from identity_service.api.public import build_jwks_router, build_public_router
from identity_service.api.public.guest import build_guest_router
from identity_service.api.public.realtime import build_realtime_router


def build_routers(deps: ApiDeps) -> list[APIRouter]:
    """Все роутеры сервиса. Зависимости собирает composition root (``wiring``)."""
    return [
        build_public_router(deps),
        build_guest_router(deps),
        build_realtime_router(deps),
        build_jwks_router(deps),
        build_internal_router(deps),
        build_admin_router(deps),
    ]
