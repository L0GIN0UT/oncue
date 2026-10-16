"""Общее для роутеров collections: use-cases, ``Cache-Control``, субъекты запроса."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from collections_service.application.commands.admin import AdminCommands
from collections_service.application.commands.author import AuthorCommands
from collections_service.application.commands.reactions import ReactionCommands
from collections_service.application.common import Actor, AdminContext
from collections_service.application.queries.collections import CollectionQueries
from collections_service.domain.access import ADMIN_PERMS, Viewer
from collections_service.settings import CollectionsLimits
from gv_auth import Principal, Sanction
from gv_core.errors import AppError
from gv_service.problems import problem, retry_after_seconds, status_for

NO_STORE = "no-store"
PUBLIC_CATALOG = "public, max-age=30"


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Use-cases для роутеров (собирает ``main.py``: слой api не знает infrastructure)."""

    service_name: str
    author: AuthorCommands
    reactions: ReactionCommands
    admin: AdminCommands
    queries: CollectionQueries
    limits: CollectionsLimits


class NoStoreRoute(APIRoute):
    """Ответы (и ошибки ``AppError``) не кэшируются, если ручка не задала своё.

    Ошибки рендерятся здесь же: в ``details.current`` ошибки ``collections.draft_conflict``
    лежит черновик автора — он не должен попасть в общий кэш.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Обёртка над обработчиком FastAPI."""
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except AppError as exc:
                response = problem(
                    status_for(exc),
                    exc.code,
                    exc.message,
                    extra={"details": exc.details} if exc.details else None,
                    retry_after=retry_after_seconds(exc.details),
                )
            if "cache-control" not in response.headers:
                response.headers["Cache-Control"] = NO_STORE
            return response

        return handler


def is_admin(principal: Principal | None) -> bool:
    """Админ сборников: ``admin.access`` + ``collections.view`` (D6)."""
    return principal is not None and not principal.is_guest and principal.has_all(*ADMIN_PERMS)


def viewer_of(principal: Principal | None) -> Viewer:
    """Субъект чтения: гость и аноним — без ``user_id`` (``me: null``, состав закрыт)."""
    if principal is None or principal.is_guest:
        return Viewer.anonymous()
    return Viewer(user_id=principal.user_id, is_admin=is_admin(principal))


def actor_of(principal: Principal, key: str) -> Actor:
    """Автор мутации (``require_user`` уже отсёк гостя)."""
    return Actor(
        user_id=principal.user_id,
        idempotency_key=key,
        sanctioned=principal.is_sanctioned(Sanction.CUSTOM_CONTENT),
        is_admin=is_admin(principal),
    )


def admin_ctx(principal: Principal, reason: str, key: str) -> AdminContext:
    """Контекст admin-мутации."""
    return AdminContext(actor_id=principal.user_id, reason=reason, idempotency_key=key)


def client_subject(request: Request) -> str:
    """Субъект лимита поиска: IP клиента (каталог публичный и кэшируемый)."""
    return f"ip:{request.client.host}" if request.client else "ip:unknown"
