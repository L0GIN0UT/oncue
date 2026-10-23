"""HTTP API progression: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1)."""

from __future__ import annotations

from fastapi import APIRouter

from gv_service import Infrastructure
from progression_service.api.admin import build_admin_router
from progression_service.api.public import build_public_router
from progression_service.application.queries import ProgressQueries


def build_api_routers(infra: Infrastructure, queries: ProgressQueries) -> list[APIRouter]:
    """Все роутеры сервиса: use-cases приходят из composition root (``main.py``)."""
    return [build_public_router(infra, queries), build_admin_router()]
