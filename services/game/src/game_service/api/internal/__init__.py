"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Каждая ручка требует сервисный токен identity с ``aud`` = game
(``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию имя сервиса) и областью ``rounds.plan``
(клиент ``match``, §2.7 плана этапа 4). Пользовательский JWT — 401, сервисный без области —
403 ``auth.forbidden``.

План раундов содержит ключи ответа (античит §12 п. 1 плана этапа 4): он уходит только в ответ
match и не попадает ни в логи, ни в тексты ошибок.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from game_service.api.common import ApiServices, NoStoreRoute
from game_service.api.internal.schemas import ModeConfigBody, RoundPlan, RoundPlanRequest
from game_service.application.queries.round_plans import RoundPlanQuery
from gv_auth import ServiceAuthenticator, ServicePrincipal
from gv_service import problem_responses

ROUNDS_PLAN_SCOPE = "rounds.plan"
"""Область сервисного токена match → game (``GV_SERVICE_CLIENTS`` identity)."""

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
            "`game.too_many_collections`, 422 `game.pool_too_small` (`details.available`), "
            "422 `game.collection_not_supported` (сборники у режима без source `collection`). "
            "`exclude_ids` избегаются по правилу источника (`random` — строго, `collection` — "
            "пока хватает остальных единиц). `overrides` (`clue_type`, `answer_format`) — "
            "переопределения лобби по `session.lobby.overridable`: 422 "
            "`game.override_not_allowed` (`details.field`), 422 `game.override_invalid` "
            "(`details.errors` валидатора ядра); пул проверяется под переопределённую загадку."
        ),
        responses=problem_responses(401, 403, 404, 422, 501, 503),
    )
    async def create_round_plan(body: RoundPlanRequest, caller: Caller) -> RoundPlan:
        plan = await c.round_plans.plan(
            RoundPlanQuery(
                mode_key=body.mode_key,
                version=body.version,
                seed=body.seed,
                rounds=body.rounds,
                collection_ids=body.collection_ids,
                exclude_ids=body.exclude_ids,
                clue_type=body.overrides.clue_type if body.overrides else None,
                answer_format=body.overrides.answer_format if body.overrides else None,
            ),
            caller=caller.client_id,
        )
        return RoundPlan.model_validate(plan)

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
        caller: Caller,  # noqa: ARG001 — зависимость нужна ради авторизации
    ) -> ModeConfigBody:
        return ModeConfigBody.model_validate(await c.round_plans.mode_version(mode_key, version))

    return router
