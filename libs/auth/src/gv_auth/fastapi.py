"""Зависимости FastAPI для аутентификации и авторизации.

Пример::

    @router.post("/v1/collections/{id}/unpublish")
    async def unpublish(
        id: UUID,
        principal: Annotated[Principal, Depends(require_perm("collections.unpublish"))],
    ) -> None: ...

Верификатор кладётся в ``app.state.jwt_verifier`` при сборке приложения.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from gv_auth.principal import Principal
from gv_auth.verifier import JwtVerifier
from gv_core.context import attach_user
from gv_core.errors import ForbiddenError, UnauthenticatedError

ACCESS_COOKIE = "gv_access"
_BEARER = "bearer "


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith(_BEARER):
        return header[len(_BEARER) :].strip() or None
    return request.cookies.get(ACCESS_COOKIE) or None


def _verifier(request: Request) -> JwtVerifier:
    verifier = getattr(request.app.state, "jwt_verifier", None)
    if not isinstance(verifier, JwtVerifier):
        raise RuntimeError("app.state.jwt_verifier не настроен")  # noqa: TRY004 — ошибка конфигурации
    return verifier


async def optional_principal(request: Request) -> Principal | None:
    """Субъект, если передан валидный токен; ``None`` для гостя.

    Невалидный токен — это ошибка (401), а не «гость»: иначе просроченный токен молча
    превращал бы пользователя в анонима.
    """
    token = _extract_token(request)
    if token is None:
        return None
    principal = await _verifier(request).verify(token)
    attach_user(principal.user_id)  # user_id попадёт в логи и события до конца запроса
    return principal


async def current_principal(request: Request) -> Principal:
    """Субъект запроса; 401, если токена нет."""
    principal = await optional_principal(request)
    if principal is None:
        raise UnauthenticatedError("требуется вход")
    return principal


def require_perm(*perms: str) -> Callable[[Request], Awaitable[Principal]]:
    """Зависимость: субъект с ВСЕМИ указанными правами, иначе 403."""
    if not perms:
        raise ValueError("укажите хотя бы одно право")

    async def dependency(request: Request) -> Principal:
        principal = await current_principal(request)
        if not principal.has_all(*perms):
            raise ForbiddenError("недостаточно прав", code="auth.forbidden")
        return principal

    return dependency
