"""Admin API (/admin/v1): модерация и кураторские сборники (план этапа 3b §2.5, D14).

Доступ — ``admin.access`` + конкретное право (миграция identity ``0007``):
``collections.view`` (чтение), ``collections.curate`` (кураторские, «рекомендуемое»),
``collections.unpublish`` (скрыть/показать), ``collections.restrict_author``. Мутации —
``Idempotency-Key`` и ``reason`` (аудит). Шаг 0a: ручки отвечают 501 до потока K.
"""

# ruff: noqa: ARG001 — заглушки этапа 3b (501) объявляют параметры контракта до реализации.

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status

from collections_service.api.schemas import (
    AdminCollectionDetail,
    AdminCollectionOut,
    AdminCollectionPage,
    CollectionStatus,
    CuratedCollectionIn,
    CuratedCollectionUpdate,
    Cursor,
    FeaturedRequest,
    Kind,
    ReasonRequest,
    RestrictionOut,
)
from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses

ADMIN_ACCESS = "admin.access"
Viewer = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "collections.view"))]
Curator = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "collections.curate"))]
Unpublisher = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "collections.unpublish"))]
Restrictor = Annotated[
    Principal, Depends(require_perm(ADMIN_ACCESS, "collections.restrict_author"))
]
CollectionId = Annotated[UUID, Path(description="Идентификатор сборника")]
UserId = Annotated[UUID, Path(description="Пользователь-автор")]
STAGE_3B = "этап 3b: реализуется в потоке K (docs/plans/stage-3b.md)"

_READ = problem_responses(401, 403, 422, 501)
_READ_ONE = problem_responses(401, 403, 404, 422, 501)
_MUTATE = problem_responses(401, 403, 404, 409, 422, 501)


def build_admin_router() -> APIRouter:
    """Админские ручки collections."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.get(
        "/collections",
        response_model=AdminCollectionPage,
        summary="Сборники (включая черновики и скрытые)",
        description="Поиск по названию и автору (нику).",
        responses=_READ,
    )
    async def admin_list(
        *,
        principal: Viewer,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        status_: Annotated[CollectionStatus | None, Query(alias="status")] = None,
        kind: Annotated[Kind | None, Query()] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AdminCollectionPage:
        raise NotImplementedYetError(STAGE_3B)

    @router.get(
        "/collections/{collection_id}",
        response_model=AdminCollectionDetail,
        summary="Сборник с составом",
        responses=_READ_ONE,
    )
    async def admin_get(collection_id: CollectionId, principal: Viewer) -> AdminCollectionDetail:
        raise NotImplementedYetError(STAGE_3B)

    @router.post(
        "/collections",
        response_model=AdminCollectionDetail,
        status_code=status.HTTP_201_CREATED,
        summary="Создать кураторский сборник",
        description=(
            "Публикуется сразу (`collections.CollectionPublished`, `kind = curated`). "
            "Ошибки — как у публикации UGC: 422 `collections.publish_invalid` "
            "(`details.errors`)."
        ),
        responses=_MUTATE,
    )
    async def admin_create(
        body: CuratedCollectionIn, principal: Curator, key: IdempotencyKey
    ) -> AdminCollectionDetail:
        raise NotImplementedYetError(STAGE_3B)

    @router.put(
        "/collections/{collection_id}",
        response_model=AdminCollectionDetail,
        summary="Новая версия кураторского сборника",
        description=(
            "409 `collections.version_conflict` — `base_version` устарела; 409 "
            "`collections.not_curated` — UGC правит только автор."
        ),
        responses=_MUTATE,
    )
    async def admin_update(
        collection_id: CollectionId,
        body: CuratedCollectionUpdate,
        principal: Curator,
        key: IdempotencyKey,
    ) -> AdminCollectionDetail:
        raise NotImplementedYetError(STAGE_3B)

    @router.post(
        "/collections/{collection_id}/hide",
        response_model=AdminCollectionOut,
        summary="Скрыть сборник",
        description=(
            "Пропадает из каталога, старт сессии в game — 404; автор видит плашку. Событие "
            "`collections.CollectionUpdated` (`status: hidden`)."
        ),
        responses=_MUTATE,
    )
    async def admin_hide(
        collection_id: CollectionId,
        body: ReasonRequest,
        principal: Unpublisher,
        key: IdempotencyKey,
    ) -> AdminCollectionOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.post(
        "/collections/{collection_id}/unhide",
        response_model=AdminCollectionOut,
        summary="Показать скрытый сборник",
        responses=_MUTATE,
    )
    async def admin_unhide(
        collection_id: CollectionId,
        body: ReasonRequest,
        principal: Unpublisher,
        key: IdempotencyKey,
    ) -> AdminCollectionOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.put(
        "/collections/{collection_id}/featured",
        response_model=AdminCollectionOut,
        summary="Отметка «рекомендуемое»",
        description="Кураторские и UGC (D14); только опубликованные.",
        responses=_MUTATE,
    )
    async def admin_featured(
        collection_id: CollectionId,
        body: FeaturedRequest,
        principal: Curator,
        key: IdempotencyKey,
    ) -> AdminCollectionOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.post(
        "/authors/{user_id}/restriction",
        response_model=RestrictionOut,
        summary="Ограничить автора",
        description="Не может создавать, править и публиковать сборники (опубликованные остаются).",
        responses=_MUTATE,
    )
    async def restrict_author(
        user_id: UserId, body: ReasonRequest, principal: Restrictor, key: IdempotencyKey
    ) -> RestrictionOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.delete(
        "/authors/{user_id}/restriction",
        response_model=RestrictionOut,
        summary="Снять ограничение автора",
        responses=_MUTATE,
    )
    async def unrestrict_author(
        user_id: UserId,
        body: Annotated[ReasonRequest, Body()],
        principal: Restrictor,
        key: IdempotencyKey,
    ) -> RestrictionOut:
        raise NotImplementedYetError(STAGE_3B)

    return router
