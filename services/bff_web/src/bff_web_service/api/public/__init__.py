"""Публичный API (/v1). На gateway: /api/web/v1/....

Шаг 0a плана этапа 2 (§2.6): только GET, схемы и заглушки (501 ``common.not_implemented``);
реализация — поток C. Токен берётся из cookie ``gv_access`` или ``Authorization: Bearer``
и передаётся сервисам как Bearer. Публичные ответы — ``public, max-age=30``, личные —
``private, no-store``.
"""

# ruff: noqa: ARG001 — параметры заглушек объявлены ради контракта OpenAPI; снять в потоке C.

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from bff_web_service.api.public.schemas import (
    Domain,
    HomePage,
    ResultsPage,
    SessionInfo,
    SoloPage,
)
from gv_core.errors import NotImplementedYetError
from gv_service import Infrastructure, problem_responses


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def _not_implemented() -> NoReturn:
    raise NotImplementedYetError("ручка ещё не реализована (план этапа 2, поток C)")


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["bff_web"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    @router.get(
        "/session",
        response_model=SessionInfo,
        summary="Кто смотрит страницу",
        description="Недействительный или истёкший токен — `subject: null`, а не ошибка.",
        responses=problem_responses(501),
    )
    async def session() -> SessionInfo:
        _not_implemented()

    @router.get(
        "/pages/home",
        response_model=HomePage,
        summary="Главная: дейлики и режимы",
        responses=problem_responses(501),
    )
    async def home_page() -> HomePage:
        _not_implemented()

    @router.get(
        "/pages/solo",
        response_model=SoloPage,
        summary="Хаб соло-режимов",
        responses=problem_responses(422, 501),
    )
    async def solo_page(domain: Annotated[Domain | None, Query()] = None) -> SoloPage:
        _not_implemented()

    @router.get(
        "/pages/results/{session_id}",
        response_model=ResultsPage,
        summary="Экран результатов сессии",
        responses=problem_responses(401, 404, 422, 501),
    )
    async def results_page(session_id: Annotated[UUID, Path()]) -> ResultsPage:
        _not_implemented()

    return router
