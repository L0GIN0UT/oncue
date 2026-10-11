"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Каждая ручка требует сервисный токен identity с ``aud`` = этот сервис
(``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию имя сервиса) и областью ``assets.link``
(D13 плана этапа 2). Верификатор — ``app.state.service_jwt_verifier`` (``create_app``).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from gv_auth import ServiceAuthenticator, ServicePrincipal
from gv_service import problem_responses
from media_pipeline_service.api.common import AssetSetItem
from media_pipeline_service.application.commands.links import IssueAssetLink
from media_pipeline_service.application.queries.asset_sets import ListAssetSets

ASSETS_LINK_SCOPE = "assets.link"
"""Область сервисного токена для ссылок и списка наборов (``GV_SERVICE_CLIENTS`` identity)."""


class AssetLinkRequest(BaseModel):
    """Запрос ссылки на уровень набора."""

    asset_set_id: UUID
    level: int = Field(ge=1, le=1000)
    subject: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[\x21-\x7e]+$",
        description=(
            "Кто запрашивает (сессия игры, матч): ключ лимита выдачи. "
            "Сервис хранит только sha256-хэш значения"
        ),
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
    caller = Annotated[
        ServicePrincipal, Depends(ServiceAuthenticator().require_service(ASSETS_LINK_SCOPE))
    ]

    @router.post(
        "/asset-links",
        response_model=AssetLinkResponse,
        summary="Выдать подписанную ссылку на уровень набора",
        responses=problem_responses(401, 403, 404, 409, 410, 422, 429, 503),
    )
    async def create_asset_link(body: AssetLinkRequest, client: caller) -> AssetLinkResponse:
        result = await issue_link.execute(
            body.asset_set_id, body.level, body.subject, client_id=client.client_id
        )
        return AssetLinkResponse(
            url=result.url, expires_at=result.expires_at, mime=result.mime, level=result.level
        )

    @router.get(
        "/asset-sets",
        response_model=list[AssetSetItem],
        summary="Наборы ассетов единицы",
        responses=problem_responses(401, 403, 422),
    )
    async def get_asset_sets(
        _: caller,
        media_item_id: UUID,
        clue_type: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)] = None,
    ) -> list[AssetSetItem]:
        views = await list_sets.execute(media_item_id, clue_type)
        return [AssetSetItem.of(v) for v in views]

    return router
