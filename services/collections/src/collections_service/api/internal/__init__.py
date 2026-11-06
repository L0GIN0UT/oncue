"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Каждая ручка требует сервисный токен identity с ``aud`` = collections
(``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию имя сервиса). ``POST /cover-items`` - область
``covers.read`` (клиент ``bff_web``). Пользовательский JWT - 401, сервисный без области - 403
``auth.forbidden``.

Публичный API id единиц не отдаёт тем, кто сборник не прошёл (D6, ADR 0028), поэтому обложки
карточек идут обходным путём: bff спрашивает здесь первую единицу публичных сборников, а ссылку
на её картинку берёт у media-pipeline сам (collections media-pipeline не вызывает).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from collections_service.api.common import ApiServices, NoStoreRoute
from gv_auth import ServiceAuthenticator, ServicePrincipal
from gv_service import problem_responses

COVERS_READ_SCOPE = "covers.read"
"""Область сервисного токена bff_web → collections (``GV_SERVICE_CLIENTS`` identity)."""
MAX_COVER_IDS = 200
"""Жёсткий предел схемы; рабочий - ``GV_COLLECTIONS__COVERS__MAX_IDS`` (422)."""

Caller = Annotated[
    ServicePrincipal, Depends(ServiceAuthenticator().require_service(COVERS_READ_SCOPE))
]


class CoverItemsRequest(BaseModel):
    """Какие сборники спрашиваем (дубли схлопываются)."""

    model_config = ConfigDict(extra="forbid")

    collection_ids: list[UUID] = Field(min_length=1, max_length=MAX_COVER_IDS)


class CoverItem(BaseModel):
    """Первая по ``position`` единица сборника."""

    collection_id: UUID
    media_item_id: UUID


class CoverItemsOut(BaseModel):
    """Только опубликованные публичные сборники с составом; остальных в ответе нет."""

    items: list[CoverItem]


def build_internal_router(c: ApiServices) -> APIRouter:
    """Ручки для bff_web: первая единица сборников под обложки."""
    router = APIRouter(prefix="/internal/v1", tags=["internal"], route_class=NoStoreRoute)

    @router.post(
        "/cover-items",
        response_model=CoverItemsOut,
        summary="Первая единица публичных сборников (для обложек карточек)",
        description=(
            "Для bff_web: `media_item_id` первой по `position` единицы каждого опубликованного "
            "ПУБЛИЧНОГО сборника из списка. Приватные (`unlisted`), черновики, скрытые, удалённые "
            "и несуществующие сборники в ответ не попадают (без различия). Не больше "
            "`GV_COLLECTIONS__COVERS__MAX_IDS` сборников (422 `collections.too_many_ids`, "
            "`details.given/max`). Сервисный токен с областью `covers.read` "
            "(audience = collections)."
        ),
        responses=problem_responses(401, 403, 422, 503),
    )
    async def cover_items(body: CoverItemsRequest, caller: Caller) -> CoverItemsOut:  # noqa: ARG001
        return CoverItemsOut.model_validate(await c.queries.cover_items(body.collection_ids))

    return router
