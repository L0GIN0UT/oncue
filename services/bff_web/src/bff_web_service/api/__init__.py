"""HTTP API bff_web: только публичный (/v1), без мутаций (D4)."""

from __future__ import annotations

from fastapi import APIRouter

from bff_web_service.api.deps import BffDeps
from bff_web_service.api.public import build_public_router


def build_routers(deps: BffDeps) -> list[APIRouter]:
    """Все роутеры сервиса. Зависимости собирает composition root (``wiring``)."""
    return [build_public_router(deps)]
