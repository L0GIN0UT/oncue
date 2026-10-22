"""Публичный API (/v1). На gateway: /api/identity/v1/....

Модуль без ``from __future__ import annotations``: зависимости объявлены внутри фабрики
роутера (замыкания), а FastAPI вычисляет строковые аннотации только по глобальным именам.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from gv_auth import Principal, require_csrf, require_same_origin
from gv_core.errors import FeatureDisabledError, UnauthenticatedError
from identity_service.api.common import (
    OAUTH_COOKIE,
    REFRESH_COOKIE,
    ApiDeps,
    ConsentIn,
    SessionUser,
    actor_of,
    clear_session_cookies,
    client_ip,
    device_token,
    session_user,
    set_device_cookie,
    set_session_cookies,
)
from identity_service.api.public.guest import end_guest_session
from identity_service.application.commands.codes import VerifiedSession
from identity_service.application.commands.oauth import PURPOSE_LINK, PURPOSE_LOGIN
from identity_service.application.dto import ChallengeIssued, RegisterCommand
from identity_service.application.security import OAuthStateError
from identity_service.domain import ConsentRequirement, Provider, SessionKind

_PASSWORD = Field(min_length=1, max_length=256)


class ServiceMeta(BaseModel):
    """Сведения о сервисе (сохранено с этапа 0 ради совместимости контракта)."""

    service: str
    status: str


class RegisterRequest(BaseModel):
    """Регистрация."""

    nickname: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=254)
    password: str = _PASSWORD
    display_name: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)
    consents: list[ConsentIn] = Field(default_factory=list, max_length=20)


class ChallengeResponse(BaseModel):
    """Код отправлен на почту (ADR 0041). Одинаков для существующего и нет адреса."""

    challenge_id: UUID
    purpose: Literal["register", "login", "reset", "password_change"]
    code_length: int
    expires_in_s: int
    resend_after_s: int


class CodeRequest(BaseModel):
    """Ввод кода из письма."""

    challenge_id: UUID
    code: str = Field(min_length=4, max_length=10, pattern=r"^[0-9]+$")


class ResendRequest(BaseModel):
    """Повторная отправка кода."""

    challenge_id: UUID


class LoginRequest(BaseModel):
    """Вход: ``login`` — e-mail или никнейм."""

    login: str = Field(min_length=1, max_length=254)
    password: str = _PASSWORD


class SessionResponse(BaseModel):
    """Сессия открыта; токены — в HttpOnly-cookie."""

    user: SessionUser
    expires_in: int


class LoginResponse(BaseModel):
    """Результат входа: сессия (``authenticated``) или нужен код с почты (``code_required``).

    При ``authenticated`` заполнены ``user`` и ``expires_in``, при ``code_required`` — только
    ``challenge``; cookie сессии при этом не выдаются.
    """

    status: Literal["authenticated", "code_required"]
    user: SessionUser | None = None
    expires_in: int | None = None
    challenge: ChallengeResponse | None = None


class ForgotRequest(BaseModel):
    """Запрос сброса пароля."""

    email: str = Field(min_length=1, max_length=254)


class ResetRequest(BaseModel):
    """Новый пароль по коду из письма."""

    challenge_id: UUID
    code: str = Field(min_length=4, max_length=10, pattern=r"^[0-9]+$")
    password: str = _PASSWORD


class PasswordChangeRequest(BaseModel):
    """Начало смены пароля в профиле: подтверждение текущим паролем."""

    current_password: str = _PASSWORD


class PasswordChangeConfirmRequest(BaseModel):
    """Завершение смены пароля: код из письма и новый пароль."""

    challenge_id: UUID
    code: str = Field(min_length=4, max_length=10, pattern=r"^[0-9]+$")
    new_password: str = _PASSWORD


class LinkedAccount(BaseModel):
    """Привязанный внешний сервис."""

    provider: str
    display: str | None
    linked_at: datetime


class MeResponse(BaseModel):
    """Профиль владельца аккаунта."""

    id: UUID
    nickname: str
    email: str
    email_verified: bool
    display_name: str
    locale: str | None
    roles: list[str]
    perms: list[str]
    linked_accounts: list[LinkedAccount]
    consents: list[ConsentIn]


class ConsentsRequest(BaseModel):
    """Принятые согласия."""

    consents: list[ConsentIn] = Field(min_length=1, max_length=20)


class DeleteMeRequest(BaseModel):
    """Удаление аккаунта с подтверждением паролем."""

    password: str = _PASSWORD


class LinkStartRequest(BaseModel):
    """Начало привязки: нужен текущий пароль (повторная аутентификация)."""

    return_to: str | None = Field(default=None, max_length=512)
    password: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Обязателен: текущий пароль. Без него — 422 auth.password_required.",
    )


class UnlinkRequest(BaseModel):
    """Отвязка сервиса: нужен текущий пароль (повторная аутентификация)."""

    password: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Обязателен: текущий пароль. Без него — 422 auth.password_required.",
    )


class AuthorizeResponse(BaseModel):
    """Куда отправить браузер."""

    authorize_url: str


def _challenge(issued: ChallengeIssued) -> ChallengeResponse:
    return ChallengeResponse(
        challenge_id=issued.challenge_id,
        purpose=issued.purpose,
        code_length=issued.code_length,
        expires_in_s=issued.expires_in_s,
        resend_after_s=issued.resend_after_s,
    )


def _consents(items: list[ConsentIn]) -> tuple[ConsentRequirement, ...]:
    return tuple(ConsentRequirement(c.kind, c.version) for c in items)


def build_public_router(deps: ApiDeps) -> APIRouter:  # noqa: PLR0915 — плоский список ручек
    """Публичные ручки."""
    uc, auth, settings = deps.use_cases, deps.auth, deps.settings
    router = APIRouter(prefix="/v1", tags=["identity"])
    same_origin = [Depends(require_same_origin)]
    # Ручки профиля — только для аккаунта: гость получает 403 auth.guest_forbidden (D5).
    Current = Annotated[Principal, Depends(auth.user)]  # noqa: N806

    def _oauth_cookie(response: Response, nonce: str) -> None:
        response.set_cookie(
            OAUTH_COOKIE,
            nonce,
            max_age=settings.oauth.state_ttl_s,
            path=settings.cookies.oauth_path,
            httponly=True,
            secure=settings.cookies.secure,
            samesite="lax",  # callback — top-level GET с oauth.yandex.ru, Lax cookie отправит
            domain=settings.cookies.domain,
        )

    def _provider(name: str) -> Provider:
        try:
            provider = Provider(name)
        except ValueError:
            raise FeatureDisabledError(
                "вход через этот сервис выключен", code="auth.provider_disabled"
            ) from None
        return provider

    async def _open_session(
        request: Request, response: Response, verified: VerifiedSession
    ) -> SessionResponse:
        # Вход в аккаунт из гостевой сессии отзывает её; устройство становится доверенным.
        await end_guest_session(deps, request)
        set_session_cookies(response, verified.session, settings)
        set_device_cookie(response, verified.device_token, settings)
        return SessionResponse(
            user=session_user(verified.session), expires_in=verified.session.expires_in
        )

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=settings.service_name, status="active")

    # --------------------------------------------------------------- вход и регистрация

    @router.post(
        "/auth/register",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ChallengeResponse,
        dependencies=same_origin,
        summary="Регистрация по никнейму, e-mail и паролю; код подтверждения уходит на почту",
    )
    async def register(body: RegisterRequest, request: Request) -> ChallengeResponse:
        # Ответ не зависит от того, был ли e-mail занят (защита от перечисления).
        issued = await uc.register(
            RegisterCommand(
                nickname=body.nickname,
                email=body.email,
                password=body.password,
                consents=_consents(body.consents),
                display_name=body.display_name,
                locale=body.locale,
                ip=client_ip(request),
            )
        )
        return _challenge(issued)

    @router.post(
        "/auth/verify-email",
        response_model=SessionResponse,
        dependencies=same_origin,
        summary="Подтвердить e-mail кодом из письма; открывает сессию и доверяет устройству",
    )
    async def verify_email(
        body: CodeRequest, request: Request, response: Response
    ) -> SessionResponse:
        verified = await uc.verify_email(body.challenge_id, body.code, ip=client_ip(request))
        return await _open_session(request, response, verified)

    @router.post(
        "/auth/login",
        response_model=LoginResponse,
        dependencies=same_origin,
        summary="Вход по e-mail или никнейму и паролю; с нового устройства нужен код с почты",
    )
    async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
        result = await uc.login(
            body.login, body.password, ip=client_ip(request), device_token=device_token(request)
        )
        if result.session is None:
            # Неизвестное устройство: cookie сессии не выдаём до ввода кода.
            assert result.challenge is not None  # noqa: S101 — инвариант LoginResult
            return LoginResponse(status="code_required", challenge=_challenge(result.challenge))
        await end_guest_session(deps, request)
        set_session_cookies(response, result.session, settings)
        return LoginResponse(
            status="authenticated",
            user=session_user(result.session),
            expires_in=result.session.expires_in,
        )

    @router.post(
        "/auth/login/confirm",
        response_model=SessionResponse,
        dependencies=same_origin,
        summary="Подтвердить вход с нового устройства кодом из письма",
    )
    async def login_confirm(
        body: CodeRequest, request: Request, response: Response
    ) -> SessionResponse:
        verified = await uc.confirm_login(body.challenge_id, body.code, ip=client_ip(request))
        return await _open_session(request, response, verified)

    @router.post(
        "/auth/codes/resend",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ChallengeResponse,
        dependencies=same_origin,
        summary="Отправить код повторно (не раньше resend_after_s)",
    )
    async def resend_code(body: ResendRequest, request: Request) -> ChallengeResponse:
        return _challenge(await uc.resend_code(body.challenge_id, ip=client_ip(request)))

    @router.post(
        "/auth/refresh",
        response_model=SessionResponse,
        dependencies=[Depends(require_csrf)],
        summary="Обновить сессию (ротация refresh-токена)",
    )
    async def refresh(request: Request, response: Response) -> SessionResponse:
        token = request.cookies.get(REFRESH_COOKIE)
        if not token:
            raise UnauthenticatedError("нет refresh-токена", code="auth.refresh_invalid")
        issued = await uc.refresh(token, kind=SessionKind.WEB)
        set_session_cookies(response, issued, settings)
        return SessionResponse(user=session_user(issued), expires_in=issued.expires_in)

    @router.post(
        "/auth/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_csrf)],
        summary="Выйти из текущей сессии",
    )
    async def logout(request: Request, response: Response) -> None:
        token = request.cookies.get(REFRESH_COOKIE)
        if token:
            await uc.logout(refresh_token=token)
        else:
            try:
                principal = await auth.optional(request)
            except UnauthenticatedError:
                principal = None
            sid = actor_of(principal).session_id if principal else None
            if sid:
                await uc.logout(session_id=sid)
        clear_session_cookies(response, settings)

    @router.post(
        "/auth/logout-all",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Выйти на всех устройствах",
    )
    async def logout_all(principal: Current, request: Request, response: Response) -> None:
        await uc.logout_all(principal.user_id, device_token=device_token(request))
        clear_session_cookies(response, settings)

    @router.post(
        "/auth/password/forgot",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ChallengeResponse,
        dependencies=same_origin,
        summary="Запросить код для сброса пароля (ответ одинаков для любого адреса)",
    )
    async def forgot(body: ForgotRequest, request: Request) -> ChallengeResponse:
        issued = await uc.request_password_reset(body.email, ip=client_ip(request))
        return _challenge(issued)

    @router.post(
        "/auth/password/reset",
        response_model=SessionResponse,
        dependencies=same_origin,
        summary=(
            "Задать новый пароль по коду из письма: прежние сессии и устройства отзываются, "
            "владелец сразу входит и доверяет это устройство"
        ),
    )
    async def reset(body: ResetRequest, request: Request, response: Response) -> SessionResponse:
        verified = await uc.reset_password(
            body.challenge_id,
            body.code,
            body.password,
            ip=client_ip(request),
        )
        return await _open_session(request, response, verified)

    @router.post(
        "/auth/password/change",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ChallengeResponse,
        dependencies=same_origin,
        summary="Начать смену пароля в профиле: текущий пароль, код уходит на почту",
    )
    async def change_password(
        body: PasswordChangeRequest, principal: Current, request: Request
    ) -> ChallengeResponse:
        issued = await uc.request_password_change(
            principal.user_id,
            body.current_password,
            session_id=actor_of(principal).session_id,
            ip=client_ip(request),
        )
        return _challenge(issued)

    @router.post(
        "/auth/password/change/confirm",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=same_origin,
        summary="Завершить смену пароля кодом; другие сессии и устройства отзываются",
    )
    async def change_password_confirm(
        body: PasswordChangeConfirmRequest,
        principal: Current,
        request: Request,
        response: Response,
    ) -> None:
        token = await uc.confirm_password_change(
            principal.user_id,
            body.challenge_id,
            body.code,
            body.new_password,
            session_id=actor_of(principal).session_id,
            ip=client_ip(request),
        )
        set_device_cookie(response, token, settings)

    # --------------------------------------------------------------- профиль

    @router.get("/me", response_model=MeResponse, summary="Мой аккаунт")
    async def me(principal: Current) -> MeResponse:
        view = await uc.get_user(principal.user_id)
        return MeResponse(
            id=view.id,
            nickname=view.nickname,
            email=view.email,
            email_verified=view.email_verified,
            display_name=view.display_name,
            locale=view.locale,
            roles=list(view.roles),
            perms=list(view.perms),
            linked_accounts=[
                LinkedAccount(provider=a.provider, display=a.display, linked_at=a.linked_at)
                for a in view.linked_accounts
            ],
            consents=[ConsentIn(kind=c.kind, version=c.version) for c in view.consents],
        )

    @router.post("/me/consents", status_code=status.HTTP_204_NO_CONTENT, summary="Принять согласия")
    async def grant_consents(body: ConsentsRequest, principal: Current) -> None:
        await uc.grant_consents(principal.user_id, _consents(body.consents))

    @router.delete(
        "/me", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить аккаунт (нужен пароль)"
    )
    async def delete_me(
        body: Annotated[DeleteMeRequest, Body()],
        principal: Current,
        request: Request,
        response: Response,
    ) -> None:
        await uc.delete_account(principal.user_id, body.password, ip=client_ip(request))
        clear_session_cookies(response, settings)

    # --------------------------------------------------------------- привязанные аккаунты

    @router.get(
        "/me/linked-accounts", response_model=list[LinkedAccount], summary="Привязанные сервисы"
    )
    async def linked_accounts(principal: Current) -> list[LinkedAccount]:
        return [
            LinkedAccount(provider=a.provider, display=a.display, linked_at=a.linked_at)
            for a in await uc.list_linked(principal.user_id)
        ]

    @router.post(
        "/me/linked-accounts/yandex",
        response_model=AuthorizeResponse,
        summary="Начать привязку Яндекс ID (нужен вход и пароль)",
    )
    async def start_link_yandex(
        principal: Current,
        request: Request,
        response: Response,
        body: LinkStartRequest | None = None,
    ) -> AuthorizeResponse:
        started = await uc.start_oauth(
            Provider.YANDEX,
            purpose=PURPOSE_LINK,
            actor=actor_of(principal),
            return_to=body.return_to if body else None,
            password=body.password if body else None,
            ip=client_ip(request),
        )
        _oauth_cookie(response, started.browser_nonce)
        return AuthorizeResponse(authorize_url=started.authorize_url)

    @router.delete(
        "/me/linked-accounts/{provider}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Отвязать сервис (нужен пароль)",
    )
    async def unlink(
        provider: str,
        principal: Current,
        request: Request,
        body: Annotated[UnlinkRequest | None, Body()] = None,
    ) -> None:
        await uc.unlink(
            principal.user_id,
            _provider(provider),
            password=body.password if body else None,
            ip=client_ip(request),
        )

    @router.get(
        "/oauth/yandex/login",
        status_code=status.HTTP_302_FOUND,
        response_class=RedirectResponse,
        summary="Вход через привязанный Яндекс ID",
    )
    async def yandex_login(
        return_to: Annotated[str | None, Query(max_length=512)] = None,
    ) -> RedirectResponse:
        started = await uc.start_oauth(
            Provider.YANDEX, purpose=PURPOSE_LOGIN, actor=None, return_to=return_to
        )
        redirect = RedirectResponse(started.authorize_url, status_code=status.HTTP_302_FOUND)
        _oauth_cookie(redirect, started.browser_nonce)
        return redirect

    @router.get(
        "/oauth/yandex/callback",
        status_code=status.HTTP_302_FOUND,
        response_class=RedirectResponse,
        summary="Возврат от Яндекс ID (привязка или вход)",
    )
    async def yandex_callback(
        request: Request,
        state: Annotated[str, Query(max_length=256)] = "",
        code: Annotated[str, Query(max_length=512)] = "",
        error: Annotated[str | None, Query(max_length=128)] = None,
    ) -> RedirectResponse:
        if error or not code:
            raise OAuthStateError("вход отменён у провайдера", code="auth.oauth_denied")
        try:
            principal = await auth.optional(request)
        except UnauthenticatedError:
            principal = None  # истёкший access не мешает: привязка проверит сессию из state
        result = await uc.complete_oauth(
            Provider.YANDEX,
            state=state,
            code=code,
            browser_nonce=request.cookies.get(OAUTH_COOKIE),
            actor=actor_of(principal) if principal else None,
            ip=client_ip(request),
        )
        target = result.return_to
        if result.purpose == PURPOSE_LINK:
            sep = "&" if "?" in target else "?"
            target = f"{target}{sep}linked={result.provider.value}"
        redirect = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
        redirect.delete_cookie(
            OAUTH_COOKIE, path=settings.cookies.oauth_path, domain=settings.cookies.domain
        )
        if result.session is not None:
            await end_guest_session(deps, request, principal)
            set_session_cookies(redirect, result.session, settings)
        return redirect

    return router


def build_jwks_router(deps: ApiDeps) -> APIRouter:
    """``/.well-known/jwks.json`` — публичные ключи для проверки JWT другими сервисами."""
    router = APIRouter(tags=["identity"])

    @router.get("/.well-known/jwks.json", summary="Публичные ключи подписи (JWKS)")
    async def jwks(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "public, max-age=300"
        return deps.use_cases.get_jwks()

    return router
