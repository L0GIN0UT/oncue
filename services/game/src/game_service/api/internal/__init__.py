"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Каждая ручка требует сервисный токен identity с ``aud`` = game
(``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию имя сервиса) и областью ``rounds.plan``
(клиент ``match``, §2.7 плана этапа 4). Пользовательский JWT — 401, сервисный без области —
403 ``auth.forbidden``.

Шаг 0a: контракт и заглушки (501 после авторизации); реализация — поток B (B2).
"""

# ruff: noqa: ARG001 — параметры заглушек нужны ради схемы и зависимости авторизации.

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from game_service.api.common import ApiServices, NoStoreRoute
from game_service.api.internal.schemas import ModeConfigBody, RoundPlan, RoundPlanRequest
from gv_auth import ServiceAuthenticator, ServicePrincipal
from gv_core.errors import NotImplementedYetError
from gv_service import problem_responses

ROUNDS_PLAN_SCOPE = "rounds.plan"
"""Область сервисного токена match → game (``GV_SERVICE_CLIENTS`` identity)."""
STAGE = "план раундов матча — поток B плана этапа 4"

Caller = Annotated[
    ServicePrincipal, Depends(ServiceAuthenticator().require_service(ROUNDS_PLAN_SCOPE))
]
ModeKeyPath = Annotated[
    str,
    Path(pattern=r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=100),
]


def build_internal_router(c: ApiServices) -> APIRouter:
    """Ручки для match: план раундов и тело опубликованного конфига."""
    router = APIRouter(prefix="/internal/v1", tags=["internal"], route_class=NoStoreRoute)

    @router.post(
        "/round-plans",
        response_model=RoundPlan,
        summary="План раундов матча",
        description=(
            "Розыгрыш через `ModeEngine.draw_rounds` с `exclude_ids` и `collection_ids` "
            "(D5): детерминирован по `seed`, только играбельные единицы (ассеты всех уровней "
            "готовы). Ошибки: 404 `game.mode_not_found` (нет опубликованной версии), 422 "
            "`game.mode_not_online` (у шаблона `session.kind = solo`), 404 "
            "`game.collection_not_found` (нет, скрыт или удалён), 422 "
            "`game.collection_required`, 422 `game.collection_domain_mismatch`, 422 "
            "`game.too_many_collections`, 422 `game.pool_too_small` (`details.available`)."
        ),
        responses=problem_responses(401, 403, 404, 422, 501, 503),
    )
    async def create_round_plan(body: RoundPlanRequest, caller: Caller) -> RoundPlan:
        raise NotImplementedYetError(STAGE)

    @router.get(
        "/modes/{mode_key}/versions/{version}",
        response_model=ModeConfigBody,
        summary="Тело опубликованной версии конфига",
        description=(
            "Для проверки шаблона и переопределений лобби в match; версии неизменяемы "
            "(кэшируется вызывающим без срока). Ошибки: 404 `game.mode_not_found` (нет такой "
            "опубликованной или архивной версии)."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def get_mode_version(
        mode_key: ModeKeyPath,
        version: Annotated[int, Path(ge=1)],
        caller: Caller,
    ) -> ModeConfigBody:
        raise NotImplementedYetError(STAGE)

    return router
