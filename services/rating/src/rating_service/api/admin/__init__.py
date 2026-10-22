"""Admin API (/admin/v1): ladder-ы, сезоны, лидерборды (ADR 0037, 0039).

Шаг 0a этапа 4b: контракт и заглушки (501 после проверки прав); реализация — поток G.
Права: ``admin.access`` + ``rating.ladders.publish`` (ladder-ы) или ``rating.seasons.manage``
(сезоны, пересборка) — identity ``0008_seed_online_perms``. Мутации — ``Idempotency-Key`` и
``reason`` (попадает в аудит).
"""

# Заглушки 0a этапа 4b: аргументы нужны схеме OpenAPI, тела придут в потоке G.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
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

ADMIN_ACCESS = "admin.access"
LADDERS_PUBLISH = "rating.ladders.publish"
SEASONS_MANAGE = "rating.seasons.manage"
LadderManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, LADDERS_PUBLISH))]
SeasonManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, SEASONS_MANAGE))]
LadderPath = Annotated[LadderKey, Path(description="Ключ ladder-а")]
_STUB = "Заглушка контракта 0a этапа 4b: реализация — поток G (rating)"


def build_admin_router() -> APIRouter:
    """Ручки админки rating."""
    router = APIRouter(prefix="/admin/v1", tags=["rating-admin"])

    @router.get(
        "/ladders",
        response_model=AdminLadderList,
        summary="Ladder-ы",
        description="Все ladder-ы с действующей и черновой версиями.",
        responses=problem_responses(401, 403, 501),
    )
    async def ladders(principal: LadderManager) -> AdminLadderList:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/ladders/{ladder}/versions",
        response_model=LadderVersionList,
        summary="Версии ladder-а",
        description="Черновик, действующая и архивные версии, новые первыми.",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def versions(ladder: LadderPath, principal: LadderManager) -> LadderVersionList:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/ladders/{ladder}/versions/{version}",
        response_model=LadderVersionBody,
        summary="Версия ladder-а с конфигом",
        description="Тело конфига по `contracts/rating/ladder.v1.json` (для diff в админке).",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def version(
        ladder: LadderPath,
        version: Annotated[int, Path(ge=1)],
        principal: LadderManager,
    ) -> LadderVersionBody:
        raise NotImplementedYetError(_STUB)

    @router.put(
        "/ladders/{ladder}/draft",
        response_model=LadderVersionBody,
        summary="Сохранить черновик",
        description=(
            "Валидация по `contracts/rating/ladder.v1.json` и инвариантам домена (звания по "
            "возрастанию, `below` растёт, ключ в теле равен ключу в пути): 422 "
            "`rating.ladder_invalid` с путями ошибок."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def save_draft(
        ladder: LadderPath,
        body: SaveDraftRequest,
        principal: LadderManager,
        key: IdempotencyKey,
    ) -> LadderVersionBody:
        raise NotImplementedYetError(_STUB)

    @router.post(
        "/ladders/{ladder}/publish",
        response_model=LadderVersionBody,
        summary="Опубликовать версию",
        description=(
            "Версия становится действующей для новых матчей; уходит `LadderPublished`. "
            "Ошибки: 404 `rating.version_not_found`, 409 `rating.version_not_draft`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def publish(
        ladder: LadderPath,
        body: PublishRequest,
        principal: LadderManager,
        key: IdempotencyKey,
    ) -> LadderVersionBody:
        raise NotImplementedYetError(_STUB)

    @router.post(
        "/ladders/{ladder}/rollback",
        response_model=LadderVersionBody,
        summary="Откатить ladder",
        description=(
            "Копия ранее опубликованной версии публикуется новой версией (история не "
            "переписывается). Ошибки: 404 `rating.version_not_found`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def rollback(
        ladder: LadderPath,
        body: RollbackRequest,
        principal: LadderManager,
        key: IdempotencyKey,
    ) -> LadderVersionBody:
        raise NotImplementedYetError(_STUB)

    @router.post(
        "/ladders/{ladder}/seasons",
        response_model=SeasonCard,
        status_code=status.HTTP_201_CREATED,
        summary="Создать сезон",
        description=(
            "Длина — из конфига ladder-а. Ошибки: 409 `rating.season_overlap` (пересечение с "
            "существующим сезоном)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def create_season(
        ladder: LadderPath,
        body: CreateSeasonRequest,
        principal: SeasonManager,
        key: IdempotencyKey,
    ) -> SeasonCard:
        raise NotImplementedYetError(_STUB)

    @router.post(
        "/seasons/{season_id}/close",
        response_model=SeasonCard,
        summary="Закрыть сезон",
        description=(
            "Мягкий сброс по конфигу, `SeasonFinished`, следующий сезон открывается "
            "автоматически. Ошибки: 404 `rating.season_not_found`, 409 "
            "`rating.season_not_active`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def close_season(
        season_id: Annotated[UUID, Path()],
        body: CloseSeasonRequest,
        principal: SeasonManager,
        key: IdempotencyKey,
    ) -> SeasonCard:
        raise NotImplementedYetError(_STUB)

    @router.post(
        "/ladders/{ladder}/leaderboard/rebuild",
        response_model=RebuildAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Пересобрать лидерборд",
        description="Ставит задачу `rating.leaderboards.rebuild` для текущего сезона.",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def rebuild(
        ladder: LadderPath,
        body: RebuildRequest,
        principal: SeasonManager,
        key: IdempotencyKey,
    ) -> RebuildAccepted:
        raise NotImplementedYetError(_STUB)

    return router
