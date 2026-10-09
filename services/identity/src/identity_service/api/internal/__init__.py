"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Вход в админку (D2): только пароль и право ``admin.access``; токены — в теле ответа,
admin хранит их в своей серверной сессии.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from identity_service.api.common import ApiDeps, IdentityRoute, SessionUser, session_user
from identity_service.application.commands.auth import ADMIN_ACCESS_PERM
from identity_service.application.sessions import IssuedSession
from identity_service.domain import SessionKind


class AdminSessionRequest(BaseModel):
    """Вход в админку: ``login`` — e-mail или никнейм."""

    login: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AdminRefreshRequest(BaseModel):
    """Обновление admin-сессии."""

    refresh_token: str = Field(min_length=1, max_length=256)


class AdminSessionResponse(BaseModel):
    """Токены admin-сессии."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: SessionUser


def _response(issued: IssuedSession) -> AdminSessionResponse:
    return AdminSessionResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=session_user(issued),
    )


def build_internal_router(deps: ApiDeps) -> APIRouter:
    """Внутренние ручки."""
    uc = deps.use_cases
    router = APIRouter(prefix="/internal/v1", tags=["internal"], route_class=IdentityRoute)

    @router.post("/admin-sessions", response_model=AdminSessionResponse, summary="Вход в админку")
    async def admin_session(body: AdminSessionRequest) -> AdminSessionResponse:
        # IP не передаём: запрос приходит от сервиса admin, лимит по IP ограничил бы всех
        # администраторов сразу. Действует лимит по аккаунту; IP-лимит — на стороне admin.
        return _response(await uc.admin_login(body.login, body.password, ip=None))

    @router.post(
        "/admin-sessions/refresh",
        response_model=AdminSessionResponse,
        summary="Обновить admin-сессию",
    )
    async def admin_refresh(body: AdminRefreshRequest) -> AdminSessionResponse:
        issued = await uc.refresh(
            body.refresh_token, kind=SessionKind.ADMIN, required_perm=ADMIN_ACCESS_PERM
        )
        return _response(issued)

    return router
