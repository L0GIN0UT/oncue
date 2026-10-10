"""Гостевая сессия: ``POST /v1/auth/guest`` (D5 плана этапа 2).

Ответ — 204 и cookie ``gv_access``, ``gv_refresh``, ``gv_csrf`` как при входе. Токен:
``sub = guest uuid7``, ``roles = ["guest"]``, ``perms = []``, ``gst = true``, ``sid``.
Модуль без ``from __future__ import annotations`` — как ``api/public/__init__.py``.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from gv_auth import Principal, extract_token, require_same_origin
from gv_core.errors import DependencyUnavailableError, UnauthenticatedError
from gv_service import problem_responses
from identity_service.api.common import REFRESH_COOKIE, ApiDeps, client_ip, set_session_cookies
from identity_service.application.commands.guest import GuestUseCases


def _guests(deps: ApiDeps) -> GuestUseCases:
    if deps.use_cases.guest is None:
        raise DependencyUnavailableError("гостевые сессии не настроены")
    return deps.use_cases.guest


async def _has_valid_access(deps: ApiDeps, request: Request) -> bool:
    """Запрос несёт действующий access-токен (подпись, срок, отзыв).

    Без CSRF: ручка только отказывает такой сессии и ничего не делает от её имени, а
    чужой сайт отсекает ``require_same_origin``.
    """
    found = extract_token(request)
    if found is None or deps.verifier is None:
        return False
    try:
        await deps.verifier.verify(found[0])
    except UnauthenticatedError:
        return False
    return True


async def end_guest_session(
    deps: ApiDeps, request: Request, principal: Principal | None = None
) -> None:
    """Вход в аккаунт из гостевой сессии отзывает её (cookie перевыдаёт сам вход).

    Ищем по refresh-cookie (её видят ручки ``/auth/*``), иначе — по ``sid`` гостевого
    access-токена (callback OAuth живёт на другом пути, refresh-cookie туда не приходит).
    """
    guests = deps.use_cases.guest
    if guests is None:
        return
    refresh = request.cookies.get(REFRESH_COOKIE)
    if refresh:
        await guests.end(refresh_token=refresh)
    elif principal is not None and principal.is_guest and principal.session_id:
        try:
            session_id = UUID(principal.session_id)
        except ValueError:
            return
        await guests.end(session_id=session_id)


def build_guest_router(deps: ApiDeps) -> APIRouter:
    """Ручка выдачи гостевой сессии."""
    router = APIRouter(prefix="/v1", tags=["identity"])

    @router.post(
        "/auth/guest",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_same_origin)],
        summary="Начать гостевую сессию",
        description=(
            "Cookie `gv_access`, `gv_refresh`, `gv_csrf` как при входе. Лимит по IP "
            "(429 `rate.limited`). При уже действующей сессии — 409 `auth.already_authenticated`."
        ),
        responses=problem_responses(403, 409, 429),
    )
    async def start_guest_session(request: Request, response: Response) -> None:
        issued = await _guests(deps).start(
            ip=client_ip(request),
            refresh_token=request.cookies.get(REFRESH_COOKIE),
            authenticated=await _has_valid_access(deps, request),
        )
        set_session_cookies(response, issued, deps.settings)
        response.headers["Cache-Control"] = "no-store"

    return router
