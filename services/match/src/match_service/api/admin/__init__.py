"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Доступ — ``admin.access`` + ``match.manage`` (identity ``0008_seed_online_perms``, §2.7 плана
этапа 4); мутации — ``Idempotency-Key`` и ``reason``. Завершение комнаты — команда
владельцу (``op_id`` = ``Idempotency-Key``), ответ — 202.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, problem_responses
from match_service.api.admin.schemas import AbortRoomRequest, AdminRoomsPage
from match_service.api.common import NoStoreRoute, RoomCodePath, ServicesProvider
from match_service.api.public.schemas import CommandAccepted, Cursor, MatchResult, Phase

ADMIN_ACCESS = "admin.access"
MATCH_MANAGE = "match.manage"
Manager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, MATCH_MANAGE))]


def build_admin_router(services: ServicesProvider) -> APIRouter:
    """Ручки админки: активные комнаты, принудительное завершение, итог матча."""
    router = APIRouter(prefix="/admin/v1", tags=["match-admin"], route_class=NoStoreRoute)

    @router.get(
        "/rooms",
        response_model=AdminRoomsPage,
        summary="Активные комнаты",
        description="Комнаты в любой фазе, кроме `closed`; фильтр по фазе.",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def list_rooms(
        principal: Manager,  # noqa: ARG001 — проверка прав
        phase: Annotated[Phase | None, Query()] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AdminRoomsPage:
        page = await services().room_queries.admin_rooms(phase, cursor, limit)
        return AdminRoomsPage.model_validate(page)

    @router.post(
        "/rooms/{code}/abort",
        response_model=CommandAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Принудительно завершить комнату",
        description=(
            "Команда уходит владельцу комнаты: матч завершается с `finish = aborted` (итог и "
            "`MatchFinished {aborted: true}`), комната — `closed`. Ошибки: 404 "
            "`match.room_not_found`, 409 `match.room_closed`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def abort_room(
        code: RoomCodePath, body: AbortRoomRequest, principal: Manager, key: IdempotencyKey
    ) -> CommandAccepted:
        outcome = await services().rooms.abort(principal.user_id, code, key, body.reason)
        return CommandAccepted.model_validate(outcome.body)

    @router.get(
        "/matches/{match_id}",
        response_model=MatchResult,
        summary="Итог матча",
        description="Ошибки: 404 `match.match_not_found`.",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def get_match(
        match_id: Annotated[UUID, Path(description="Идентификатор матча")],
        principal: Manager,  # noqa: ARG001 — проверка прав
    ) -> MatchResult:
        return MatchResult.model_validate(await services().matches.get(match_id))

    return router
