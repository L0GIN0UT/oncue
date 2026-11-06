"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Каждая ручка требует сервисный токен identity с ``aud`` = этот сервис
(``GV_INTERNAL_AUTH__AUDIENCE``, по умолчанию имя сервиса) и областью ``assets.link``
(D13 плана этапа 2). Верификатор — ``app.state.service_jwt_verifier`` (``create_app``).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from gv_auth import ServiceAuthenticator, ServicePrincipal
from gv_service import problem_responses
from media_pipeline_service.api.common import AssetSetItem
from media_pipeline_service.api.ugc import register_ugc_internal_routes
from media_pipeline_service.application.commands.links import IssueAssetLink, IssueCoverLinks
from media_pipeline_service.application.commands.ugc import UgcUseCases
from media_pipeline_service.application.queries.asset_sets import ListAssetSets

ASSETS_LINK_SCOPE = "assets.link"
"""Область сервисного токена для ссылок и списка наборов (``GV_SERVICE_CLIENTS`` identity)."""
COVER_LINK_SCOPE = "assets.cover_link"
"""Область сервисного токена для пакетной выдачи ссылок на обложки (только bff_web)."""


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


class CoverLinksRequest(BaseModel):
    """Запрос ссылок на обложки единиц (предел размера пакета - в настройках сервиса)."""

    model_config = ConfigDict(extra="forbid")

    media_item_ids: list[UUID] = Field(min_length=1, max_length=200)


class CoverLinkItem(BaseModel):
    """Ссылка на обложку единицы."""

    media_item_id: UUID
    url: str
    expires_at: datetime
    mime: str


class CoverLinksResponse(BaseModel):
    """Ссылки только для единиц с готовой обложкой; остальные в ответ не попадают."""

    items: list[CoverLinkItem]


def build_internal_router(
    issue_link: IssueAssetLink,
    list_sets: ListAssetSets,
    ugc: UgcUseCases,
    issue_covers: IssueCoverLinks,
) -> APIRouter:
    """Ручки для game/match/bff: ссылки на ассеты, список наборов, обложки карточек."""
    router = APIRouter(prefix="/internal/v1", tags=["internal"])
    caller = Annotated[
        ServicePrincipal, Depends(ServiceAuthenticator().require_service(ASSETS_LINK_SCOPE))
    ]
    covers_caller = Annotated[
        ServicePrincipal, Depends(ServiceAuthenticator().require_service(COVER_LINK_SCOPE))
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

    @router.post(
        "/cover-links",
        response_model=CoverLinksResponse,
        summary="Выдать ссылки на обложки списка единиц (карточки каталога)",
        description=(
            "Каталожный набор `artwork`, уровень 1. Срок ссылки кратен окну "
            "`GV_MEDIA__COVER_LINK_WINDOW_S`: повторные запросы в окне дают тот же URL. "
            "Единицы без готовой обложки или заблокированные в ответ не попадают."
        ),
        responses=problem_responses(401, 403, 422, 503),
    )
    async def create_cover_links(
        body: CoverLinksRequest, client: covers_caller
    ) -> CoverLinksResponse:
        links = await issue_covers.execute(body.media_item_ids, client_id=client.client_id)
        return CoverLinksResponse(
            items=[
                CoverLinkItem(
                    media_item_id=link.media_item_id,
                    url=link.url,
                    expires_at=link.expires_at,
                    mime=link.mime,
                )
                for link in links
            ]
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

    register_ugc_internal_routes(router, ugc)
    return router
