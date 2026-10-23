"""Admin API (/admin/v1): ladder-ы, сезоны, лидерборды (ADR 0037, 0039).

Права: ``admin.access`` + ``rating.ladders.publish`` (ladder-ы) или ``rating.seasons.manage``
(сезоны, пересборка) — identity ``0008_seed_online_perms``. Мутации — ``Idempotency-Key`` и
``reason`` (попадает в аудит); ответ без кэша.
"""

# ruff: noqa: ARG001 — параметр principal нужен ради зависимости с проверкой права.
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Response, status

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, problem_responses
from rating_service.api.admin.schemas import (
    AdminLadderList,
    CloseSeasonRequest,
    CreateSeasonRequest,
    LadderKey,
    LadderVersionBody,
    LadderVersionList,
    PublishRequest,
    RebuildAccepted,
    RebuildRequest,
    RollbackRequest,
    SaveDraftRequest,
    SeasonCard,
)
from rating_service.application.commands.ladders import LadderCommands, LadderQueries
from rating_service.application.commands.rebuild import RebuildLeaderboard
from rating_service.application.commands.seasons import SeasonCommands
from rating_service.application.common import AdminContext

ADMIN_ACCESS = "admin.access"
LADDERS_PUBLISH = "rating.ladders.publish"
SEASONS_MANAGE = "rating.seasons.manage"
LadderManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, LADDERS_PUBLISH))]
SeasonManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, SEASONS_MANAGE))]
LadderPath = Annotated[LadderKey, Path(description="Ключ ladder-а")]
NO_STORE = "no-store"


def _ctx(principal: Principal, reason: str, key: str) -> AdminContext:
    return AdminContext(actor_id=principal.user_id, reason=reason, idempotency_key=key)


def build_admin_router(
    ladder_commands: LadderCommands,
    ladder_queries: LadderQueries,
    season_commands: SeasonCommands,
    rebuild: RebuildLeaderboard,
) -> APIRouter:
    """Ручки админки rating."""
    router = APIRouter(prefix="/admin/v1", tags=["rating-admin"])

    @router.get(
        "/ladders",
        response_model=AdminLadderList,
        summary="Ladder-ы",
        description="Все ladder-ы с действующей и черновой версиями.",
        responses=problem_responses(401, 403),
    )
    async def ladders(response: Response, principal: LadderManager) -> AdminLadderList:
        response.headers["Cache-Control"] = NO_STORE
        return AdminLadderList.model_validate(await ladder_queries.list())

    @router.get(
        "/ladders/{ladder}/versions",
        response_model=LadderVersionList,
        summary="Версии ladder-а",
        description="Черновик, действующая и архивные версии, новые первыми.",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def versions(
        ladder: LadderPath, response: Response, principal: LadderManager
    ) -> LadderVersionList:
        response.headers["Cache-Control"] = NO_STORE
        return LadderVersionList.model_validate(await ladder_queries.versions(ladder))

    @router.get(
        "/ladders/{ladder}/versions/{version}",
        response_model=LadderVersionBody,
        summary="Версия ladder-а с конфигом",
        description="Тело конфига по `contracts/rating/ladder.v1.json` (для diff в админке).",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def version(
        ladder: LadderPath,
        version: Annotated[int, Path(ge=1)],
        response: Response,
        principal: LadderManager,
    ) -> LadderVersionBody:
        response.headers["Cache-Control"] = NO_STORE
        return LadderVersionBody.model_validate(await ladder_queries.version(ladder, version))

    @router.put(
        "/ladders/{ladder}/draft",
        response_model=LadderVersionBody,
        summary="Сохранить черновик",
        description=(
            "Валидация по `contracts/rating/ladder.v1.json` и инвариантам домена (звания по "
            "возрастанию, `below` растёт, ключ в теле равен ключу в пути): 422 "
            "`rating.ladder_invalid` с путями ошибок."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def save_draft(
        ladder: LadderPath,
        body: SaveDraftRequest,
        response: Response,
        principal: LadderManager,
        key: IdempotencyKey,
    ) -> LadderVersionBody:
        response.headers["Cache-Control"] = NO_STORE
        result = await ladder_commands.save_draft(
            ladder, body.config, ctx=_ctx(principal, body.reason, key)
        )
        return LadderVersionBody.model_validate(result)

    @router.post(
        "/ladders/{ladder}/publish",
        response_model=LadderVersionBody,
        summary="Опубликовать версию",
        description=(
            "Версия становится действующей для новых матчей; уходит `LadderPublished`. "
            "Ошибки: 404 `rating.version_not_found`, 409 `rating.version_not_draft`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def publish(
        ladder: LadderPath,
        body: PublishRequest,
        response: Response,
        principal: LadderManager,
        key: IdempotencyKey,
    ) -> LadderVersionBody:
        response.headers["Cache-Control"] = NO_STORE
        result = await ladder_commands.publish(
            ladder, version=body.version, ctx=_ctx(principal, body.reason, key)
        )
        return LadderVersionBody.model_validate(result)

    @router.post(
        "/ladders/{ladder}/rollback",
        response_model=LadderVersionBody,
        summary="Откатить ladder",
        description=(
            "Копия ранее опубликованной версии публикуется новой версией (история не "
            "переписывается). Ошибки: 404 `rating.version_not_found`, 409 "
            "`rating.version_not_published` (версия никогда не публиковалась)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def rollback(
        ladder: LadderPath,
        body: RollbackRequest,
        response: Response,
        principal: LadderManager,
        key: IdempotencyKey,
    ) -> LadderVersionBody:
        response.headers["Cache-Control"] = NO_STORE
        result = await ladder_commands.rollback(
            ladder, to_version=body.to_version, ctx=_ctx(principal, body.reason, key)
        )
        return LadderVersionBody.model_validate(result)

    @router.post(
        "/ladders/{ladder}/seasons",
        response_model=SeasonCard,
        status_code=status.HTTP_201_CREATED,
        summary="Создать сезон",
        description=(
            "Длина — из конфига ladder-а. Ошибки: 409 `rating.season_overlap` (пересечение с "
            "существующим сезоном)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def create_season(
        ladder: LadderPath,
        body: CreateSeasonRequest,
        response: Response,
        principal: SeasonManager,
        key: IdempotencyKey,
    ) -> SeasonCard:
        response.headers["Cache-Control"] = NO_STORE
        result = await season_commands.create(
            ladder, starts_at=body.starts_at, ctx=_ctx(principal, body.reason, key)
        )
        return SeasonCard.model_validate(result)

    @router.post(
        "/seasons/{season_id}/close",
        response_model=SeasonCard,
        summary="Закрыть сезон",
        description=(
            "Мягкий сброс по конфигу, `SeasonFinished`, следующий сезон открывается "
            "автоматически. Ошибки: 404 `rating.season_not_found`, 409 "
            "`rating.season_not_active`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def close_season(
        season_id: Annotated[UUID, Path()],
        body: CloseSeasonRequest,
        response: Response,
        principal: SeasonManager,
        key: IdempotencyKey,
    ) -> SeasonCard:
        response.headers["Cache-Control"] = NO_STORE
        result = await season_commands.close(season_id, ctx=_ctx(principal, body.reason, key))
        return SeasonCard.model_validate(result)

    @router.post(
        "/ladders/{ladder}/leaderboard/rebuild",
        response_model=RebuildAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Пересобрать лидерборд",
        description=(
            "Ставит запрос на пересборку лидерборда текущего сезона; выполняет задача "
            "`rating.leaderboards.rebuild`. Ошибки: 404 `rating.ladder_not_found`, "
            "404 `rating.season_not_found`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def rebuild_leaderboard(
        ladder: LadderPath,
        body: RebuildRequest,
        response: Response,
        principal: SeasonManager,
        key: IdempotencyKey,
    ) -> RebuildAccepted:
        response.headers["Cache-Control"] = NO_STORE
        result = await rebuild.execute(ladder, ctx=_ctx(principal, body.reason, key))
        return RebuildAccepted.model_validate(result)

    return router
