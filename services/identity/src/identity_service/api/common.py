"""Общие части HTTP API identity: зависимости, cookie сессии."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from fastapi import Request, Response
from pydantic import BaseModel, Field

from gv_auth import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    Authenticator,
    JwtVerifier,
    Principal,
    new_csrf_token,
)
from identity_service.application.container import IdentityUseCases
from identity_service.application.dto import Actor
from identity_service.application.sessions import IssuedSession
from identity_service.settings import Settings

REFRESH_COOKIE = "gv_refresh"
OAUTH_COOKIE = "gv_oauth"


@dataclass(frozen=True)
class ApiDeps:
    """Что нужно роутерам: use-cases, аутентификация, настройки."""

    use_cases: IdentityUseCases
    auth: Authenticator
    settings: Settings
    verifier: JwtVerifier | None = None
    """Верификатор ``auth`` — для проверок без CSRF (``POST /v1/auth/guest`` только
    отказывает действующей сессии и ничего не делает от её имени)."""


def client_ip(request: Request) -> str | None:
    """IP клиента (uvicorn учитывает X-Forwarded-For от gateway)."""
    return request.client.host if request.client else None


def actor_of(principal: Principal) -> Actor:
    """Субъект токена → актор use-case."""
    try:
        session_id = UUID(principal.session_id) if principal.session_id else None
    except ValueError:
        session_id = None
    return Actor(principal.user_id, principal.roles, principal.perms, session_id)


class SessionUser(BaseModel):
    """Субъект сессии: пользователь или гость (у гостя ``id`` — guest id, имени нет)."""

    id: UUID
    display_name: str
    roles: list[str]
    perms: list[str]
    kind: Literal["user", "guest"] = "user"


def session_user(issued: IssuedSession) -> SessionUser:
    """Субъект из выданной сессии."""
    return SessionUser(
        id=issued.user_id,
        display_name=issued.display_name,
        roles=sorted(issued.roles),
        perms=sorted(issued.perms),
        kind="guest" if issued.is_guest else "user",
    )


class ConsentIn(BaseModel):
    """Согласие определённой редакции."""

    kind: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=32)


def _cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "secure": settings.cookies.secure,
        "samesite": "lax",
        "domain": settings.cookies.domain,
    }


def set_session_cookies(response: Response, issued: IssuedSession, settings: Settings) -> None:
    """Access (Path=/), refresh (только ручки auth) и новый CSRF-токен (§8 п.2–3)."""
    kw = _cookie_kwargs(settings)
    refresh_ttl_s = issued.refresh_ttl_s or settings.jwt.refresh_ttl_s
    response.set_cookie(
        ACCESS_COOKIE,
        issued.access_token,
        max_age=issued.expires_in,
        path="/",
        httponly=True,
        **kw,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=refresh_ttl_s,
        path=settings.cookies.refresh_path,
        httponly=True,
        **kw,
    )
    # CSRF-токен читает JS фронтенда и кладёт в X-CSRF-Token, поэтому без HttpOnly.
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(),
        max_age=refresh_ttl_s,
        path="/",
        httponly=False,
        **kw,
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    """Удалить cookie сессии."""
    kw = _cookie_kwargs(settings)
    response.delete_cookie(ACCESS_COOKIE, path="/", httponly=True, **kw)
    response.delete_cookie(REFRESH_COOKIE, path=settings.cookies.refresh_path, httponly=True, **kw)
    response.delete_cookie(CSRF_COOKIE, path="/", **kw)
