"""Зависимости FastAPI для аутентификации и авторизации.

Пример::

    @router.post("/v1/collections/{id}/unpublish")
    async def unpublish(
        id: UUID,
        principal: Annotated[Principal, Depends(require_perm("collections.unpublish"))],
    ) -> None: ...

Верификатор кладётся в ``app.state.jwt_verifier`` при сборке приложения. Сервису, которому
нужен свой верификатор (identity проверяет токены по собственным ключам), — ``Authenticator``.

Токен берётся из ``Authorization: Bearer`` или cookie ``gv_access``. Для cookie на
небезопасных методах обязательна проверка CSRF (``gv_auth.csrf``): браузер подставляет
cookie сам, в том числе в запросы с чужих сайтов.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from fastapi import Request

from gv_auth.csrf import enforce_csrf
from gv_auth.principal import Principal
from gv_auth.verifier import JwtVerifier
from gv_core.context import attach_user
from gv_core.errors import ForbiddenError, UnauthenticatedError

ACCESS_COOKIE = "gv_access"
_BEARER = "bearer "


class TokenSource(StrEnum):
    """Откуда взят access-токен."""

    HEADER = "header"
    COOKIE = "cookie"


def extract_token(request: Request) -> tuple[str, TokenSource] | None:
    """Токен запроса и его источник; заголовок важнее cookie."""
    header = request.headers.get("authorization")
    if header and header.lower().startswith(_BEARER):
        token = header[len(_BEARER) :].strip()
        return (token, TokenSource.HEADER) if token else None
    cookie = request.cookies.get(ACCESS_COOKIE)
    return (cookie, TokenSource.COOKIE) if cookie else None


def _state_verifier(request: Request) -> JwtVerifier:
    verifier = getattr(request.app.state, "jwt_verifier", None)
    if not isinstance(verifier, JwtVerifier):
        raise RuntimeError("app.state.jwt_verifier не настроен")  # noqa: TRY004 — ошибка конфигурации
    return verifier


class Authenticator:
    """Набор зависимостей FastAPI поверх конкретного верификатора.

    Args:
        verifier: Верификатор; ``None`` — брать ``app.state.jwt_verifier``.
    """

    def __init__(self, verifier: JwtVerifier | None = None) -> None:
        self._verifier = verifier

    def _get_verifier(self, request: Request) -> JwtVerifier:
        return self._verifier if self._verifier is not None else _state_verifier(request)

    async def optional(self, request: Request) -> Principal | None:
        """Субъект, если передан валидный токен; ``None`` для гостя.

        Невалидный токен — это ошибка (401), а не «гость»: иначе просроченный токен молча
        превращал бы пользователя в анонима.
        """
        found = extract_token(request)
        if found is None:
            return None
        token, source = found
        if source is TokenSource.COOKIE:
            enforce_csrf(request)
        principal = await self._get_verifier(request).verify(token)
        attach_user(principal.user_id)  # user_id попадёт в логи и события до конца запроса
        return principal

    async def current(self, request: Request) -> Principal:
        """Субъект запроса; 401, если токена нет."""
        principal = await self.optional(request)
        if principal is None:
            raise UnauthenticatedError("требуется вход")
        return principal

    def require_perm(self, *perms: str) -> Callable[[Request], Awaitable[Principal]]:
        """Зависимость: субъект с ВСЕМИ указанными правами, иначе 403."""
        if not perms:
            raise ValueError("укажите хотя бы одно право")

        async def dependency(request: Request) -> Principal:
            principal = await self.current(request)
            if not principal.has_all(*perms):
                raise ForbiddenError("недостаточно прав", code="auth.forbidden")
            return principal

        return dependency


_DEFAULT = Authenticator()


async def optional_principal(request: Request) -> Principal | None:
    """Субъект по ``app.state.jwt_verifier`` или ``None`` (см. ``Authenticator.optional``)."""
    return await _DEFAULT.optional(request)


async def current_principal(request: Request) -> Principal:
    """Субъект запроса; 401, если токена нет."""
    return await _DEFAULT.current(request)


def require_perm(*perms: str) -> Callable[[Request], Awaitable[Principal]]:
    """Зависимость: субъект с ВСЕМИ указанными правами, иначе 403."""
    return _DEFAULT.require_perm(*perms)
