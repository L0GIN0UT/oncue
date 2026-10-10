"""Гостевая сессия: ``POST /v1/auth/guest`` (D5 плана этапа 2).

Шаг 0a: контракт и заглушка (501 ``common.not_implemented``); реализация — поток C
(файл принадлежит ему, §3 плана). Ответ — 204 и cookie ``gv_access``, ``gv_refresh``,
``gv_csrf`` как при входе; токен: ``sub = guest uuid7``, ``roles = ["guest"]``,
``perms = []``, ``gst = true``.
"""

from fastapi import APIRouter, Depends, status

from gv_auth import require_same_origin
from gv_core.errors import NotImplementedYetError
from gv_service import problem_responses
from identity_service.api.common import ApiDeps


def build_guest_router(deps: ApiDeps) -> APIRouter:  # noqa: ARG001 — DI для потока C
    """Ручка выдачи гостевой сессии."""
    router = APIRouter(prefix="/v1", tags=["identity"])

    @router.post(
        "/auth/guest",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_same_origin)],
        summary="Начать гостевую сессию",
        description=(
            "Cookie `gv_access`, `gv_refresh`, `gv_csrf` как при входе. Лимит по IP. "
            "При уже действующей сессии — 409 `auth.already_authenticated`."
        ),
        responses=problem_responses(403, 409, 429, 501),
    )
    async def start_guest_session() -> None:
        raise NotImplementedYetError("гостевые сессии — поток C плана этапа 2")

    return router
