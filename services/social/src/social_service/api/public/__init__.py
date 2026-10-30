"""Публичный API (/v1). На gateway: /api/social/v1/....

Контракт этапа 5c (``docs/plans/stage-5.md`` §10.1, ADR 0050-0052): друзья и приватность
(``friends.py``), комментарии, жалобы и свои санкции (``discussion.py``).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel

from gv_service import Infrastructure
from social_service.api.discussion_services import DiscussionServices
from social_service.api.public.discussion import register_discussion_routes
from social_service.api.public.friends import register_friend_routes
from social_service.api.public.online import register_online_routes
from social_service.application.friends import FriendsService
from social_service.application.online_count import OnlineCounter


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(
    infra: Infrastructure,
    friends: Callable[[], FriendsService],
    discussion: DiscussionServices,
    online: Callable[[], OnlineCounter],
) -> APIRouter:
    """Публичные ручки (``friends`` собирает use-case друзей на каждый запрос)."""
    router = APIRouter(prefix="/v1", tags=["social"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    register_friend_routes(router, friends)
    register_online_routes(router, online)
    register_discussion_routes(router, discussion)
    return router
