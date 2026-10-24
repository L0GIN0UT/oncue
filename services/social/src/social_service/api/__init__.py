"""HTTP API social: публичный (/v1), внутренний (/internal/v1), админский (/admin/v1)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from gv_service import Infrastructure
from social_service.api.admin import build_admin_router
from social_service.api.admin.services import AdminServices
from social_service.api.discussion_services import DiscussionServices
from social_service.api.public import build_public_router
from social_service.application.friends import FriendsService
from social_service.application.sanctions import SanctionService


def build_routers(
    infra: Infrastructure,
    friends: Callable[[], FriendsService],
    discussion: DiscussionServices,
    admin: AdminServices,
    sanctions: SanctionService,
) -> list[APIRouter]:
    """Все роутеры сервиса (use-cases приходят из composition root, ``main.py``)."""
    return [
        build_public_router(infra, friends, discussion),
        build_admin_router(admin, sanctions),
    ]
