"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Каждая ручка требует сервисный токен identity с ``aud`` = progression
(``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию имя сервиса). ``GET /levels`` — область
``levels.read`` (клиент ``bff_web``). Пользовательский JWT — 401, сервисный без области —
403 ``auth.forbidden``.

Ручка раздаёт уровень по произвольным ``user_ids``, поэтому браузеру она не публикуется:
какие игроки показываются зрителю (комната, лобби), решает bff. Ledger не читается.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from gv_auth import ServiceAuthenticator, ServicePrincipal
from gv_service import problem_responses
from progression_service.api.internal.schemas import PlayerLevel, PlayerLevels
from progression_service.application.queries import ProgressQueries

LEVELS_READ_SCOPE = "levels.read"
"""Область сервисного токена bff_web → progression: уровни игроков."""
MAX_LEVELS_USERS = 100
"""Жёсткий предел схемы; рабочий — ``GV_PROGRESSION__LEVELS_MAX_USERS`` (422)."""

LevelsCaller = Annotated[
    ServicePrincipal, Depends(ServiceAuthenticator().require_service(LEVELS_READ_SCOPE))
]


def build_internal_router(queries: ProgressQueries) -> APIRouter:
    """Ручки для bff_web."""
    router = APIRouter(prefix="/internal/v1", tags=["internal"])

    @router.get(
        "/levels",
        response_model=PlayerLevels,
        summary="Уровни игроков пачкой",
        description=(
            "Для bff_web (игроки комнаты и лобби): уровень и опыт внутри уровня по списку "
            "`user_ids` (повторяющийся параметр). Сервисный токен с областью `levels.read` "
            "(audience = progression). Не больше `GV_PROGRESSION__LEVELS_MAX_USERS` игроков "
            "(422 `progression.too_many_users`, `details.given/max`), лимит "
            "`GV_PROGRESSION__LEVELS_PER_MIN` на клиента (429 + `Retry-After`). Скрытый "
            "профиль в ответ не попадает, игрок без опыта — уровень 1. Нет конфига — 503."
        ),
        responses=problem_responses(401, 403, 422, 429, 503),
    )
    async def levels(
        caller: LevelsCaller,
        response: Response,
        user_ids: Annotated[
            list[UUID],
            Query(min_length=1, max_length=MAX_LEVELS_USERS, description="Игроки"),
        ],
    ) -> PlayerLevels:
        response.headers["Cache-Control"] = "no-store"
        rows = await queries.levels(caller.client_id, user_ids)
        return PlayerLevels(
            items=[
                PlayerLevel(user_id=r.user_id, level=r.level, xp_in_level=r.xp_in_level)
                for r in rows
            ]
        )

    return router
