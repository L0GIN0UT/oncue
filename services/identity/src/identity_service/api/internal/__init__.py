"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Вход в админку (D2): только пароль и право ``admin.access``; токены — в теле ответа,
admin хранит их в своей серверной сессии. Ручки admin-сессий требуют сервисный токен
с областью ``admin_sessions`` (D13 плана этапа 2): сеть перестаёт быть единственной защитой.

``POST /service-tokens`` — client credentials: аутентификация — сами учётные данные клиента.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from gv_auth import ServicePrincipal
from gv_service import problem_responses
from identity_service.api.common import ApiDeps, SessionUser, session_user
from identity_service.application.commands.auth import ADMIN_ACCESS_PERM
from identity_service.application.sessions import IssuedSession
from identity_service.domain import SessionKind


class AdminSessionRequest(BaseModel):
    """Вход в админку: ``login`` — e-mail или никнейм."""

    login: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    client_ip: IPvAnyAddress | None = Field(
        default=None,
        description=(
            "IP браузера администратора (admin берёт его из своего доверенного прокси). "
            "Лимит попыток по IP и по подсети аккаунта считается по нему, как у входа на "
            "сайт. Без него действует только лимит по аккаунту."
        ),
    )


class AdminRefreshRequest(BaseModel):
    """Обновление или отзыв admin-сессии."""

    refresh_token: str = Field(min_length=1, max_length=256)


class AdminSessionResponse(BaseModel):
    """Токены admin-сессии."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: SessionUser
    identity_version: int = Field(
        description=(
            "Версия агрегата пользователя на момент выдачи (та же, что ``version`` в событиях "
            "UserRolesChanged): события с версией не больше этой уже учтены в ``user``."
        )
    )


class ServiceTokenRequest(BaseModel):
    """Client credentials сервиса; ``audience`` — вызываемый сервис."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    client_secret: str = Field(min_length=1, max_length=256)
    audience: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)


class ServiceTokenResponse(BaseModel):
    """Сервисный JWT (``sub = svc:<client_id>``, ``aud``, ``scope``, TTL 600 с)."""

    access_token: str
    token_type: Literal["Bearer"]
    expires_in: int


def _response(issued: IssuedSession) -> AdminSessionResponse:
    return AdminSessionResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=session_user(issued),
        identity_version=issued.identity_version,
    )


def build_internal_router(deps: ApiDeps) -> APIRouter:
    """Внутренние ручки."""
    uc = deps.use_cases
    router = APIRouter(prefix="/internal/v1", tags=["internal"])
    admin_client = Annotated[
        ServicePrincipal, Depends(deps.service_auth.require_service("admin_sessions"))
    ]

    @router.post(
        "/admin-sessions",
        response_model=AdminSessionResponse,
        summary="Вход в админку",
        responses=problem_responses(401, 403, 429),
    )
    async def admin_session(body: AdminSessionRequest, _: admin_client) -> AdminSessionResponse:
        # Запрос приходит от сервиса admin из доверенной сети: IP клиента — из тела, а не
        # адрес соединения (иначе лимит по IP ограничил бы всех администраторов сразу).
        ip = str(body.client_ip) if body.client_ip is not None else None
        return _response(await uc.admin_login(body.login, body.password, ip=ip))

    @router.post(
        "/admin-sessions/refresh",
        response_model=AdminSessionResponse,
        summary="Обновить admin-сессию",
        responses=problem_responses(401, 403),
    )
    async def admin_refresh(body: AdminRefreshRequest, _: admin_client) -> AdminSessionResponse:
        issued = await uc.refresh(
            body.refresh_token, kind=SessionKind.ADMIN, required_perm=ADMIN_ACCESS_PERM
        )
        return _response(issued)

    @router.post(
        "/admin-sessions/revoke",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Завершить admin-сессию (выход из админки)",
        description=(
            "Идемпотентно: неизвестный, уже отозванный или не admin-токен — тоже 204. "
            "access-токены сессии перестают приниматься (denylist sid)."
        ),
        responses=problem_responses(401, 403),
    )
    async def admin_revoke(body: AdminRefreshRequest, _: admin_client) -> None:
        await uc.logout(refresh_token=body.refresh_token, kind=SessionKind.ADMIN)

    @router.post(
        "/service-tokens",
        response_model=ServiceTokenResponse,
        summary="Сервисный токен (client credentials)",
        description=(
            "Ошибки: 401 `auth.client_invalid`, 403 `auth.audience_forbidden`; "
            "лимит по `client_id` (429)."
        ),
        responses=problem_responses(401, 403, 422, 429),
    )
    async def service_token(body: ServiceTokenRequest, response: Response) -> ServiceTokenResponse:
        issued = await uc.issue_service_token.execute(
            body.client_id, body.client_secret, body.audience
        )
        # Токен — учётные данные (RFC 6749 §5.1): промежуточные узлы его не кэшируют.
        response.headers["Cache-Control"] = "no-store"
        return ServiceTokenResponse(
            access_token=issued.token,
            token_type="Bearer",  # noqa: S106 — тип токена (RFC 6750), не секрет
            expires_in=issued.expires_in,
        )

    return router
