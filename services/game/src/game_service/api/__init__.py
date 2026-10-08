"""HTTP API game: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from game_service.api.public import build_public_router
from gv_service import Infrastructure


def build_routers(infra: Infrastructure) -> list[APIRouter]:
    """Все роутеры сервиса (DI через ``infra``)."""
    return [build_public_router(infra)]
