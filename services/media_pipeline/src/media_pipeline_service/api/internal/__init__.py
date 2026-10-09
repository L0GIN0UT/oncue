"""Internal API (/internal/v1): только для других сервисов внутри сети (D8: без токена,
gateway отдаёт 404 на /internal)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from media_pipeline_service.api.common import AssetSetItem, gone_on_blocked, problem_responses
from media_pipeline_service.application.commands.links import IssueAssetLink
from media_pipeline_service.application.dto import IssuedLink
from media_pipeline_service.application.queries.asset_sets import ListAssetSets


class AssetLinkRequest(BaseModel):
    """Запрос ссылки на уровень набора."""

    asset_set_id: UUID
    level: int = Field(ge=1, le=1000)
    subject: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[\x21-\x7e]+$",
        description="Кто запрашивает (сессия игры, матч): ключ лимита выдачи",
    )


class AssetLinkResponse(BaseModel):
    """Подписанная ссылка."""

    url: str
    expires_at: datetime
    mime: str
    level: int


def build_internal_router(issue_link: IssueAssetLink, list_sets: ListAssetSets) -> APIRouter:
    """Ручки для game/match: ссылки на ассеты и список наборов."""
    router = APIRouter(prefix="/internal/v1", tags=["internal"])

    @router.post(
        "/asset-links",
        response_model=AssetLinkResponse,
        summary="Выдать подписанную ссылку на уровень набора",
        responses=problem_responses(404, 409, 410, 422, 429, 503),
    )
    async def create_asset_link(body: AssetLinkRequest) -> AssetLinkResponse | JSONResponse:
        async def issue() -> IssuedLink:
            return await issue_link.execute(body.asset_set_id, body.level, body.subject)

        result = await gone_on_blocked(issue)
        if isinstance(result, JSONResponse):
            return result
        return AssetLinkResponse(
            url=result.url, expires_at=result.expires_at, mime=result.mime, level=result.level
        )

    @router.get(
        "/asset-sets",
        response_model=list[AssetSetItem],
        summary="Наборы ассетов единицы",
    )
    async def get_asset_sets(
        media_item_id: UUID,
        clue_type: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)] = None,
    ) -> list[AssetSetItem]:
        views = await list_sets.execute(media_item_id, clue_type)
        return [AssetSetItem.of(v) for v in views]

    return router
