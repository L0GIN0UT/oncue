"""Admin API (/admin/v1): модерация и кураторские сборники (план этапа 3b §2.5, D14).

Доступ — ``admin.access`` + конкретное право (миграция identity ``0007``):
``collections.view`` (чтение), ``collections.curate`` (кураторские, «рекомендуемое»),
``collections.unpublish`` (скрыть/показать), ``collections.restrict_author``. Мутации —
``Idempotency-Key`` и ``reason`` (аудит ``admin_actions``).
"""

# ruff: noqa: ARG001 — параметр principal нужен ради зависимости с проверкой права.

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status

from collections_service.api.common import ApiServices, NoStoreRoute, admin_ctx
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
from collections_service.domain.model import DraftBody
from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, Reason, problem_responses

ADMIN_ACCESS = "admin.access"
Viewer = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "collections.view"))]
Curator = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "collections.curate"))]
Unpublisher = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "collections.unpublish"))]
Restrictor = Annotated[
    Principal, Depends(require_perm(ADMIN_ACCESS, "collections.restrict_author"))
]
CollectionId = Annotated[UUID, Path(description="Идентификатор сборника")]
UserId = Annotated[UUID, Path(description="Пользователь-автор")]

_READ = problem_responses(401, 403, 422)
_READ_ONE = problem_responses(401, 403, 404, 422)
_MUTATE = problem_responses(401, 403, 404, 409, 422)


def _body(data: CuratedCollectionIn | CuratedCollectionUpdate) -> DraftBody:
    """Тело кураторского сборника как тело черновика (те же проверки публикации)."""
    raw = data.model_dump(mode="json", exclude={"reason", "domain", "base_version"})
    return DraftBody.from_json(raw)


def build_admin_router(c: ApiServices) -> APIRouter:
    """Админские ручки collections."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"], route_class=NoStoreRoute)

    @router.get(
        "/collections",
        response_model=AdminCollectionPage,
        summary="Сборники (включая черновики и скрытые)",
        description="Поиск по названию и автору (нику); удалённые не показываются.",
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
        data = await c.queries.admin_list(
            q=q, status=status_, kind=kind, cursor=cursor, limit=limit
        )
        return AdminCollectionPage.model_validate(data)

    @router.get(
        "/collections/{collection_id}",
        response_model=AdminCollectionDetail,
        summary="Сборник с составом",
        description="Опубликованный состав; у ни разу не опубликованного — состав черновика.",
        responses=_READ_ONE,
    )
    async def admin_get(collection_id: CollectionId, principal: Viewer) -> AdminCollectionDetail:
        return AdminCollectionDetail.model_validate(await c.queries.admin_detail(collection_id))

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
        data = await c.admin.create_curated(
            admin_ctx(principal, body.reason, key), domain=body.domain, body=_body(body)
        )
        return AdminCollectionDetail.model_validate(data)

    @router.put(
        "/collections/{collection_id}",
        response_model=AdminCollectionDetail,
        summary="Новая версия кураторского сборника",
        description=(
            "409 `collections.version_conflict` — `base_version` устарела "
            "(`details.current_version`); 409 `collections.not_curated` — UGC правит только "
            "автор; 422 `collections.publish_invalid`."
        ),
        responses=_MUTATE,
    )
    async def admin_update(
        collection_id: CollectionId,
        body: CuratedCollectionUpdate,
        principal: Curator,
        key: IdempotencyKey,
    ) -> AdminCollectionDetail:
        data = await c.admin.update_curated(
            admin_ctx(principal, body.reason, key),
            collection_id,
            base_version=body.base_version,
            body=_body(body),
        )
        return AdminCollectionDetail.model_validate(data)

    @router.post(
        "/collections/{collection_id}/hide",
        response_model=AdminCollectionOut,
        summary="Скрыть сборник",
        description=(
            "Пропадает из каталога, старт сессии в game — 404; автор видит плашку. Событие "
            "`collections.CollectionUpdated` (`status: hidden`); повтор — без события. 409 "
            "`collections.not_published` — сборник ещё не публиковался."
        ),
        responses=_MUTATE,
    )
    async def admin_hide(
        collection_id: CollectionId,
        body: ReasonRequest,
        principal: Unpublisher,
        key: IdempotencyKey,
    ) -> AdminCollectionOut:
        data = await c.admin.set_hidden(
            admin_ctx(principal, body.reason, key), collection_id, hidden=True
        )
        return AdminCollectionOut.model_validate(data)

    @router.post(
        "/collections/{collection_id}/unhide",
        response_model=AdminCollectionOut,
        summary="Показать скрытый сборник",
        description="Событие `collections.CollectionUpdated` (`status: published`).",
        responses=_MUTATE,
    )
    async def admin_unhide(
        collection_id: CollectionId,
        body: ReasonRequest,
        principal: Unpublisher,
        key: IdempotencyKey,
    ) -> AdminCollectionOut:
        data = await c.admin.set_hidden(
            admin_ctx(principal, body.reason, key), collection_id, hidden=False
        )
        return AdminCollectionOut.model_validate(data)

    @router.delete(
        "/collections/{collection_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Удалить пользовательский сборник",
        description=(
            "Для сборников, которые автор не может удалить сам (застрявшие черновики). "
            "Черновик, опубликованный и скрытый; опубликованный даёт "
            "`collections.CollectionDeleted`. Повтор — 204 без события. Кураторский — 409 "
            "`collections.not_ugc`. Право `collections.unpublish`, причина в `reason` (аудит)."
        ),
        responses=_MUTATE,
    )
    async def admin_delete(
        collection_id: CollectionId,
        reason: Reason,
        principal: Unpublisher,
        key: IdempotencyKey,
    ) -> None:
        await c.admin.delete_user_collection(admin_ctx(principal, reason, key), collection_id)

    @router.put(
        "/collections/{collection_id}/featured",
        response_model=AdminCollectionOut,
        summary="Отметка «рекомендуемое»",
        description=(
            "Кураторские и UGC (D14); только опубликованные (409 `collections.not_published`). "
            "Без `rank` — в конце блока (1000). Событий нет; каталог `sort=featured`."
        ),
        responses=_MUTATE,
    )
    async def admin_featured(
        collection_id: CollectionId,
        body: FeaturedRequest,
        principal: Curator,
        key: IdempotencyKey,
    ) -> AdminCollectionOut:
        data = await c.admin.set_featured(
            admin_ctx(principal, body.reason, key),
            collection_id,
            featured=body.featured,
            rank=body.rank,
        )
        return AdminCollectionOut.model_validate(data)

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
        data = await c.admin.restrict(admin_ctx(principal, body.reason, key), user_id)
        return RestrictionOut.model_validate(data)

    @router.delete(
        "/authors/{user_id}/restriction",
        response_model=RestrictionOut,
        summary="Снять ограничение автора",
        description="Идемпотентно: без ограничения — тот же ответ `restricted: false`.",
        responses=_MUTATE,
    )
    async def unrestrict_author(
        user_id: UserId,
        body: Annotated[ReasonRequest, Body()],
        principal: Restrictor,
        key: IdempotencyKey,
    ) -> RestrictionOut:
        data = await c.admin.unrestrict(admin_ctx(principal, body.reason, key), user_id)
        return RestrictionOut.model_validate(data)

    return router
