"""Публичный API (/v1). На gateway: /api/library/v1/....

План этапа 3b §2.5, §6 (L5). Все ручки — только пользователь (``require_user``: гость —
403 ``auth.guest_forbidden``); мутации — ``Idempotency-Key``, cookie-аутентификация — с
Origin и CSRF (``gv_auth``). Ответы личные — ``Cache-Control: no-store`` (``NoStoreRoute``).
Медиатека приватна: чужая полка — 404 ``library.shelf_not_found``.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel

from gv_auth import Principal, require_user
from gv_service import IdempotencyKey, problem_responses
from library_service.api.common import ApiServices, NoStoreRoute
from library_service.api.schemas import (
    CreateShelfRequest,
    Cursor,
    ItemShelvesBatchOut,
    ItemShelvesOut,
    RenameShelfRequest,
    SaveItemRequest,
    SaveResult,
    SaveSource,
    ShelfItemsPage,
    ShelfOut,
    ShelfRef,
)
from library_service.domain.shelves import SaveSource as SaveSourceEnum

User = Annotated[Principal, Depends(require_user)]
ShelfRefPath = Annotated[ShelfRef, Path(description="`system:<key>` или uuid своей полки")]
ShelfId = Annotated[UUID, Path(description="Своя полка")]
MediaItemId = Annotated[UUID, Path(description="Единица каталога")]

_READ = problem_responses(401, 403, 404, 422)
_MUTATE = problem_responses(401, 403, 404, 409, 422, 429)


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(services: ApiServices) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["library"], route_class=NoStoreRoute)
    commands, queries = services.commands, services.queries

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=services.service_name, status="ok")

    @router.get(
        "/shelves",
        response_model=list[ShelfOut],
        summary="Полки пользователя",
        description="Системные (из конфига, D11) — всегда, даже пустые; затем свои.",
        responses=_READ,
    )
    async def list_shelves(user: User) -> list[dict[str, Any]]:
        return await queries.shelves(user.user_id)

    @router.post(
        "/shelves",
        response_model=ShelfOut,
        status_code=status.HTTP_201_CREATED,
        summary="Создать свою полку",
        description=(
            "Ошибки: 409 `library.shelves_limit` (`details.max`), 422 "
            "`library.title_invalid` (`details.reason` — код `ugc.*`)."
        ),
        responses=_MUTATE,
    )
    async def create_shelf(
        body: CreateShelfRequest, user: User, key: IdempotencyKey
    ) -> dict[str, Any]:
        return await commands.create_shelf(user.user_id, body.title, key)

    @router.patch(
        "/shelves/{shelf_id}",
        response_model=ShelfOut,
        summary="Переименовать свою полку",
        responses=_MUTATE,
    )
    async def rename_shelf(
        shelf_id: ShelfId, body: RenameShelfRequest, user: User, key: IdempotencyKey
    ) -> dict[str, Any]:
        return await commands.rename_shelf(user.user_id, shelf_id, body.title, key)

    @router.delete(
        "/shelves/{shelf_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Удалить свою полку",
        description="Единицы полки — `library.ItemRemoved` с `source: shelf_deleted`.",
        responses=_MUTATE,
    )
    async def delete_shelf(shelf_id: ShelfId, user: User, key: IdempotencyKey) -> Response:
        await commands.delete_shelf(user.user_id, shelf_id, key)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/shelves/{shelf_ref}/items",
        response_model=ShelfItemsPage,
        summary="Единицы полки",
        responses=_READ,
    )
    async def shelf_items(
        shelf_ref: ShelfRefPath,
        user: User,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        return await queries.items(user.user_id, shelf_ref, cursor, limit)

    @router.put(
        "/shelves/{shelf_ref}/items/{media_item_id}",
        response_model=SaveResult,
        summary="Сохранить единицу на полку",
        description=(
            "Идемпотентно (повтор — `changed: false`, без события). Системная полка с "
            "`exclusive_with` убирает единицу с исключающих полок (`removed_from`). Ошибки: "
            "422 `library.item_unavailable` (неизвестна или заблокирована), 422 "
            "`library.domain_mismatch` (домен единицы не из доменов полки), 409 "
            "`library.shelf_full`, 429 `library.rate_limited`."
        ),
        responses=_MUTATE,
    )
    async def save_item(
        shelf_ref: ShelfRefPath,
        media_item_id: MediaItemId,
        body: SaveItemRequest,
        user: User,
        key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await commands.save_item(
            user.user_id, shelf_ref, media_item_id, SaveSourceEnum(body.source), key
        )

    @router.delete(
        "/shelves/{shelf_ref}/items/{media_item_id}",
        response_model=SaveResult,
        summary="Убрать единицу с полки",
        description="Идемпотентно; `source` — экран, с которого убрали.",
        responses=_MUTATE,
    )
    async def remove_item(
        shelf_ref: ShelfRefPath,
        media_item_id: MediaItemId,
        user: User,
        key: IdempotencyKey,
        source: Annotated[SaveSource, Query()] = "library",
    ) -> dict[str, Any]:
        return await commands.remove_item(
            user.user_id, shelf_ref, media_item_id, SaveSourceEnum(source), key
        )

    @router.get(
        "/items/shelves",
        response_model=ItemShelvesBatchOut,
        summary="На каких полках единицы (пачкой)",
        description=(
            "`ids` повторяется в запросе; дубли схлопываются, по записи на единицу "
            "(пустой `shelves` — нигде не лежит). Больше `item_shelves_batch_max` единиц — "
            "422 `library.batch_too_large`."
        ),
        responses=_READ,
    )
    async def items_shelves(
        user: User,
        ids: Annotated[list[UUID], Query(min_length=1, max_length=1000)],
    ) -> dict[str, Any]:
        return await queries.items_shelves(user.user_id, ids)

    @router.get(
        "/items/{media_item_id}/shelves",
        response_model=ItemShelvesOut,
        summary="На каких полках единица",
        responses=_READ,
    )
    async def item_shelves(media_item_id: MediaItemId, user: User) -> dict[str, Any]:
        return await queries.item_shelves(user.user_id, media_item_id)

    return router
