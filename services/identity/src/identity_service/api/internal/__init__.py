"""Internal API (/internal/v1): только для других сервисов внутри сети (gateway отдаёт 404).

Вход в админку (D2): только пароль и право ``admin.access``; токены — в теле ответа,
admin хранит их в своей серверной сессии. Ручки admin-сессий требуют сервисный токен
с областью ``admin_sessions`` (D13 плана этапа 2): сеть перестаёт быть единственной защитой.

``POST /service-tokens`` — client credentials: аутентификация — сами учётные данные клиента.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from gv_auth import ServicePrincipal
from gv_service import problem_responses
from identity_service.api.common import ApiDeps, SessionUser, client_ip, session_user
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


USERS_EMAILS_READ_SCOPE = "users.emails.read"
"""Область сервисного токена для ``POST /users/emails`` (клиент notifications)."""

MAX_EMAIL_LOOKUP_IDS = 500

USERS_ACTIVE_LIST_SCOPE = "users.active.list"
"""Область сервисного токена для ``GET /users/active`` (клиент notifications, ADR 0069)."""

ACTIVE_PAGE_DEFAULT = 1000
"""Верхняя граница параметра ``limit`` в схеме; реальный предел пачки - настройка сервиса."""


class UserEmailsRequest(BaseModel):
    """Набор пользователей; дубли id схлопываются."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[UUID] = Field(min_length=1, max_length=MAX_EMAIL_LOOKUP_IDS)


class UserEmailItem(BaseModel):
    """Подтверждённый адрес пользователя."""

    user_id: UUID
    email: str


class UserEmailsResponse(BaseModel):
    """Только подходящие пользователи; причины пропуска не различаются, порядок любой."""

    items: list[UserEmailItem]


class ActiveUsersResponse(BaseModel):
    """Страница активных пользователей: только идентификаторы по возрастанию."""

    items: list[UUID]
    next_cursor: UUID | None = Field(
        description="Значение `after` следующего запроса; `null` - последняя страница."
    )


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
        "/users/emails",
        response_model=UserEmailsResponse,
        summary="Подтверждённые адреса пользователей (рассылки)",
        description=(
            "Сервисный токен со scope `users.emails.read`. Неизвестные, удалённые и "
            "пользователи без подтверждённого e-mail молча пропускаются. 429 — лимит "
            "вызовов на `client_id`."
        ),
        responses=problem_responses(401, 403, 422, 429, 503),
    )
    async def user_emails(
        body: UserEmailsRequest,
        response: Response,
        principal: Annotated[
            ServicePrincipal,
            Depends(deps.service_auth.require_service(USERS_EMAILS_READ_SCOPE)),
        ],
    ) -> UserEmailsResponse:
        found = await uc.list_verified_emails.execute(
            principal.client_id, principal.token_id, body.user_ids
        )
        response.headers["Cache-Control"] = "no-store"
        return UserEmailsResponse(items=[UserEmailItem(user_id=i, email=e) for i, e in found])

    @router.get(
        "/users/active",
        response_model=ActiveUsersResponse,
        summary="Активные пользователи постранично (аудитория уведомлений)",
        description=(
            "Сервисный токен со scope `users.active.list`. Только идентификаторы активных "
            "(не удалённых) пользователей по возрастанию; `limit` усекается до настройки "
            "`GV_USERS_ACTIVE__MAX_PAGE`. 429 - лимит вызовов на `client_id`."
        ),
        responses=problem_responses(401, 403, 422, 429, 503),
    )
    async def active_users(
        response: Response,
        principal: Annotated[
            ServicePrincipal,
            Depends(deps.service_auth.require_service(USERS_ACTIVE_LIST_SCOPE)),
        ],
        after: Annotated[
            UUID | None, Query(description="Курсор: `next_cursor` прошлой страницы.")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=ACTIVE_PAGE_DEFAULT * 5)] = ACTIVE_PAGE_DEFAULT,
    ) -> ActiveUsersResponse:
        page = await uc.list_active_users.execute(
            principal.client_id, principal.token_id, after=after, limit=limit
        )
        response.headers["Cache-Control"] = "no-store"
        return ActiveUsersResponse(items=page.ids, next_cursor=page.next_cursor)

    @router.post(
        "/service-tokens",
        response_model=ServiceTokenResponse,
        summary="Сервисный токен (client credentials)",
        description=(
            "Ошибки: 401 `auth.client_invalid`, 403 `auth.audience_forbidden`; 429 — лимит "
            "неудач по (`client_id`, IP) или успешных выдач по `client_id`."
        ),
        responses=problem_responses(401, 403, 422, 429),
    )
    async def service_token(
        body: ServiceTokenRequest, request: Request, response: Response
    ) -> ServiceTokenResponse:
        issued = await uc.issue_service_token.execute(
            body.client_id, body.client_secret, body.audience, source_ip=client_ip(request)
        )
        # Токен — учётные данные (RFC 6749 §5.1): промежуточные узлы его не кэшируют.
        response.headers["Cache-Control"] = "no-store"
        return ServiceTokenResponse(
            access_token=issued.token,
            token_type="Bearer",  # noqa: S106 — тип токена (RFC 6750), не секрет
            expires_in=issued.expires_in,
        )

    return router
