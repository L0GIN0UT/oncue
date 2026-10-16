"""Публичный API (/v1). На gateway: /api/library/v1/....

Шаг 0a этапа 3b: схемы и коды ошибок зафиксированы, ручки отвечают 501 до потока L
(docs/plans/stage-3b.md §2.5, §6). Все ручки — только пользователь (``require_user``);
мутации — ``Idempotency-Key``, cookie-аутентификация — с Origin и CSRF (``gv_auth``).
Ответы личные — ``Cache-Control: no-store``.
"""

# ruff: noqa: ARG001 — заглушки этапа 3b (501) объявляют параметры контракта до реализации.

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel

from gv_auth import Principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Infrastructure, problem_responses
from library_service.api.schemas import (
    CreateShelfRequest,
    Cursor,
    ItemShelvesOut,
    RenameShelfRequest,
    SaveItemRequest,
    SaveResult,
    SaveSource,
    ShelfItemsPage,
    ShelfOut,
    ShelfRef,
)

User = Annotated[Principal, Depends(require_user)]
ShelfRefPath = Annotated[ShelfRef, Path(description="`system:<key>` или uuid своей полки")]
ShelfId = Annotated[UUID, Path(description="Своя полка")]
MediaItemId = Annotated[UUID, Path(description="Единица каталога")]
STAGE_3B = "этап 3b: реализуется в потоке L (docs/plans/stage-3b.md)"

_READ = problem_responses(401, 403, 404, 422, 501)
_MUTATE = problem_responses(401, 403, 404, 409, 422, 429, 501)


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["library"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    @router.get(
        "/shelves",
        response_model=list[ShelfOut],
        summary="Полки пользователя",
        description="Системные (из конфига, D11) — всегда, даже пустые; затем свои.",
        responses=_READ,
    )
    async def list_shelves(user: User) -> list[ShelfOut]:
        raise NotImplementedYetError(STAGE_3B)

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
    async def create_shelf(body: CreateShelfRequest, user: User, key: IdempotencyKey) -> ShelfOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.patch(
        "/shelves/{shelf_id}",
        response_model=ShelfOut,
        summary="Переименовать свою полку",
        responses=_MUTATE,
    )
    async def rename_shelf(
        shelf_id: ShelfId, body: RenameShelfRequest, user: User, key: IdempotencyKey
    ) -> ShelfOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.delete(
        "/shelves/{shelf_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Удалить свою полку",
        description="Единицы полки — `library.ItemRemoved` с `source: shelf_deleted`.",
        responses=_MUTATE,
    )
    async def delete_shelf(shelf_id: ShelfId, user: User, key: IdempotencyKey) -> None:
        raise NotImplementedYetError(STAGE_3B)

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
    ) -> ShelfItemsPage:
        raise NotImplementedYetError(STAGE_3B)

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
    ) -> SaveResult:
        raise NotImplementedYetError(STAGE_3B)

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
    ) -> SaveResult:
        raise NotImplementedYetError(STAGE_3B)

    @router.get(
        "/items/{media_item_id}/shelves",
        response_model=ItemShelvesOut,
        summary="На каких полках единица",
        responses=_READ,
    )
    async def item_shelves(media_item_id: MediaItemId, user: User) -> ItemShelvesOut:
        raise NotImplementedYetError(STAGE_3B)

    return router
