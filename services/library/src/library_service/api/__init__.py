"""HTTP API library: публичный (/v1). Внутреннего и админского API на этапе 3b нет."""

from __future__ import annotations

from fastapi import APIRouter

from library_service.api.common import ApiServices
from library_service.api.public import build_public_router


def build_api_routers(services: ApiServices) -> list[APIRouter]:
    """Все роутеры сервиса."""
    return [build_public_router(services)]
