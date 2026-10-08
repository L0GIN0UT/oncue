"""Публичный API (/v1). На gateway: /api/library/v1/...."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from gv_service import Infrastructure


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["library"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    return router
