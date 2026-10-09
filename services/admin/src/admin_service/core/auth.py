"""Вход в админку через identity (D2 плана этапа 1: только логин и пароль, без своих паролей).

Поток: форма входа → ``POST identity /internal/v1/admin-sessions {login, password}`` →
access-токен проверяется по JWKS (``gv_auth.JwtVerifier``) → зеркальный пользователь по
``identity_user_id`` → права из токена (``PermissionSync``) → токены в серверной сессии.
Права берутся из ПРОВЕРЕННОГО токена, а не из JSON ответа: подпись identity — граница доверия.
"""

# ruff: noqa: ARG002 — сигнатуры переопределяемых методов Django и бэкенда аутентификации
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest

from admin_service.core.models import AdminUser
from admin_service.core.permissions import STAFF_PERM, PermissionSync, django_perm
from admin_service.core.service_client import (
    ServiceError,
    SessionExpiredError,
    problem_to_error,
)
from gv_auth import JwtVerifier, Principal, http_jwks_loader
from gv_core.errors import AppError

log = structlog.get_logger(__name__)

SESSION_KEY = "gv_identity"
REFRESH_MARGIN_S = 30
_SERVER_ERROR = 500
_TOO_MANY = 429
IDENTITY_ERROR_ATTR = "gv_identity_error"


@dataclass(frozen=True, slots=True)
class IdentitySession:
    """Ответ identity на вход или refresh."""

    access_token: str
    refresh_token: str
    expires_in: int
    display_name: str

    @classmethod
    def parse(cls, body: Any) -> IdentitySession:
        """Разобрать ответ ``admin-sessions``; неожиданная форма — ``TypeError``/``KeyError``."""
        if not isinstance(body, dict):
            raise TypeError("ответ identity — не объект")
        raw_user = body.get("user")
        user: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
        return cls(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            expires_in=int(body["expires_in"]),
            display_name=str(user.get("display_name") or "")[:80],
        )


def _identity_post(path: str, payload: dict[str, str], request_id: str) -> IdentitySession:
    url = f"{settings.GV_IDENTITY_URL.rstrip('/')}{path}"
    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=settings.GV_SERVICE_TIMEOUT_S,
            headers={"X-Request-Id": request_id},
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise ServiceError("identity", None, "service.unavailable", "identity недоступен") from exc
    if response.is_error:
        raise problem_to_error("identity", response)
    try:
        return IdentitySession.parse(response.json())
    except (ValueError, KeyError, TypeError) as exc:
        raise ServiceError(
            "identity", response.status_code, "service.bad_response", "Некорректный ответ identity"
        ) from exc


_verifiers: dict[tuple[str, str, str], JwtVerifier] = {}


def verifier() -> JwtVerifier:
    """Верификатор access-токенов (кэш JWKS живёт в процессе)."""
    key = (settings.GV_JWKS_URL, settings.GV_JWT_ISSUER, settings.GV_JWT_AUDIENCE)
    if key not in _verifiers:
        _verifiers[key] = JwtVerifier(http_jwks_loader(key[0]), issuer=key[1], audience=key[2])
    return _verifiers[key]


def verify_access(token: str) -> Principal:
    """Проверить access-токен по JWKS identity."""
    principal: Principal = async_to_sync(verifier().verify)(token)
    return principal


def _store(session: SessionBase, data: IdentitySession) -> None:
    session[SESSION_KEY] = {
        "access": data.access_token,
        "refresh": data.refresh_token,
        "expires_at": time.time() + data.expires_in,
    }


def _mirror(principal: Principal, display_name: str) -> AdminUser:
    user, created = AdminUser.objects.get_or_create(
        identity_user_id=principal.user_id,
        defaults={"username": f"identity-{principal.user_id}"},
    )
    if created:
        user.set_unusable_password()  # своих паролей в админке нет
    user.display_name = display_name or user.display_name
    user.is_active = True
    PermissionSync.apply(user, roles=principal.roles, perms=principal.perms)
    return user


def _accept(data: IdentitySession) -> Principal | None:
    """Проверить токен из ответа identity; ``None`` — токен не принят."""
    try:
        principal = verify_access(data.access_token)
    except AppError as exc:
        log.warning("admin.login_token_rejected", code=exc.code)
        return None
    if STAFF_PERM not in principal.perms:
        log.warning("admin.login_without_access", user_id=str(principal.user_id))
        return None
    return principal


class IdentityAuthBackend(BaseBackend):
    """Бэкенд аутентификации Django поверх identity."""

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> AdminUser | None:
        """Вход по логину (e-mail или никнейм) и паролю через identity."""
        if request is None or not username or not password:
            return None
        from admin_service.core.middleware import request_id

        try:
            data = _identity_post(
                "/internal/v1/admin-sessions",
                {"login": username, "password": password},
                request_id(request),
            )
        except ServiceError as exc:
            # 401/403/422 — неверный логин, пароль или нет admin.access: одно сообщение формы.
            if exc.status is None or exc.status >= _SERVER_ERROR or exc.status == _TOO_MANY:
                setattr(request, IDENTITY_ERROR_ATTR, exc.message)
            log.info("admin.login_rejected", code=exc.code, status=exc.status)
            return None
        principal = _accept(data)
        if principal is None:
            return None
        user = _mirror(principal, data.display_name)
        _store(request.session, data)
        log.info("admin.login", user_id=str(principal.user_id))
        return user

    def get_user(self, user_id: int) -> AdminUser | None:
        """Пользователь сессии; отключённый или без доступа — разлогинивается."""
        user = AdminUser.objects.filter(pk=user_id).first()
        if user is None or not user.is_active or not user.is_staff:
            return None
        return user

    def get_all_permissions(self, user_obj: Any, obj: Any = None) -> set[str]:
        """Права identity как права Django: ``identity.<perm>``."""
        if not isinstance(user_obj, AdminUser) or not user_obj.is_active:
            return set()
        return {django_perm(p) for p in user_obj.identity_perms}

    def has_perm(self, user_obj: Any, perm: str, obj: Any = None) -> bool:
        """Право есть в снимке identity."""
        return perm in self.get_all_permissions(user_obj, obj)

    def has_module_perms(self, user_obj: Any, app_label: str) -> bool:
        """Доступ к разделу django-admin — только суперпользователю (роль admin)."""
        return False


def access_token(request: HttpRequest) -> str:
    """Действующий access-токен пользователя; истекает — обновляется через refresh.

    Raises:
        SessionExpiredError: токенов нет или identity отказал в refresh.
    """
    from admin_service.core.middleware import request_id

    stored = request.session.get(SESSION_KEY)
    if not isinstance(stored, dict) or "access" not in stored:
        raise SessionExpiredError
    if float(stored.get("expires_at", 0)) - REFRESH_MARGIN_S > time.time():
        return str(stored["access"])
    try:
        data = _identity_post(
            "/internal/v1/admin-sessions/refresh",
            {"refresh_token": str(stored["refresh"])},
            request_id(request),
        )
    except ServiceError as exc:
        log.info("admin.refresh_failed", code=exc.code, status=exc.status)
        if exc.status is None or exc.status >= _SERVER_ERROR:
            raise  # identity недоступен — это не повод выкидывать из сессии
        raise SessionExpiredError from exc
    principal = _accept(data)
    user = request.user
    if (
        principal is None
        or not isinstance(user, AdminUser)
        or principal.user_id != user.identity_user_id
    ):
        raise SessionExpiredError
    PermissionSync.apply(user, roles=principal.roles, perms=principal.perms)
    _store(request.session, data)
    return data.access_token
