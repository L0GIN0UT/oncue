"""Вход в админку через identity (D2 плана этапа 1: только логин и пароль, без своих паролей).

Поток: форма входа → ``POST identity /internal/v1/admin-sessions {login, password,
client_ip}`` → access-токен проверяется по JWKS (``gv_auth.JwtVerifier``) → зеркальный
пользователь по ``identity_user_id`` → права из токена (``PermissionSync``) → токены
в серверной сессии (зашифрованы, ``core.session_crypto``).
Права берутся из ПРОВЕРЕННОГО токена, а не из JSON ответа: подпись identity — граница доверия.

``identity_version`` из ответа поднимает версию зеркала (``max``): событие
``UserRolesChanged``, опубликованное до входа и доставленное после, права не откатит.
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
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import HttpRequest

from admin_service.core.models import AdminUser
from admin_service.core.permissions import STAFF_PERM, PermissionSync, django_perm
from admin_service.core.service_client import (
    ServiceError,
    SessionExpiredError,
    problem_to_error,
)
from admin_service.core.session_crypto import decrypt, encrypt
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
    identity_version: int | None = None

    @classmethod
    def parse(cls, body: Any) -> IdentitySession:
        """Разобрать ответ ``admin-sessions``; неожиданная форма — ``TypeError``/``KeyError``."""
        if not isinstance(body, dict):
            raise TypeError("ответ identity — не объект")
        raw_user = body.get("user")
        user: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
        # Поле добавлено в контракт позже: старый identity его не присылает — версию не трогаем.
        raw_version = body.get("identity_version")
        return cls(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            expires_in=int(body["expires_in"]),
            display_name=str(user.get("display_name") or "")[:80],
            identity_version=None if raw_version is None else int(raw_version),
        )


def _identity_call(path: str, payload: dict[str, str], request_id: str) -> httpx.Response:
    url = f"{settings.GV_IDENTITY_URL.rstrip('/')}{path}"
    return httpx.post(
        url,
        json=payload,
        timeout=settings.GV_SERVICE_TIMEOUT_S,
        headers={"X-Request-Id": request_id},
        follow_redirects=False,
    )


def _identity_post(path: str, payload: dict[str, str], request_id: str) -> IdentitySession:
    try:
        response = _identity_call(path, payload, request_id)
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


@dataclass(frozen=True, slots=True)
class StoredTokens:
    """Токены identity из сессии (уже расшифрованные)."""

    access: str
    refresh: str
    expires_at: float

    @property
    def fresh(self) -> bool:
        """Access-токен ещё действует с запасом ``REFRESH_MARGIN_S``."""
        return self.expires_at - REFRESH_MARGIN_S > time.time()


def store_tokens(session: SessionBase, data: IdentitySession) -> None:
    """Сохранить токены identity в сессии (зашифрованными)."""
    # В БД (django_session) — только шифротекст: дамп базы не даёт действующих токенов.
    session[SESSION_KEY] = {
        "access_enc": encrypt(data.access_token),
        "refresh_enc": encrypt(data.refresh_token),
        "expires_at": time.time() + data.expires_in,
    }


def _tokens(stored: object) -> StoredTokens:
    """Токены из значения сессии.

    Raises:
        SessionExpiredError: токенов нет, формат старый (открытый) или ключ сменился.
    """
    if not isinstance(stored, dict):
        raise SessionExpiredError
    return StoredTokens(
        access=decrypt(stored.get("access_enc")),
        refresh=decrypt(stored.get("refresh_enc")),
        expires_at=float(stored.get("expires_at", 0)),
    )


def _sync(
    user: AdminUser, principal: Principal, identity_version: int | None, *, login: bool
) -> None:
    """Применить права из токена и поднять версию зеркала под блокировкой строки.

    Блокировка — та же, что у потребителя событий (``core.events``): версия и права
    меняются согласованно, без «потерянного» обновления.
    """
    with transaction.atomic():
        locked = AdminUser.objects.select_for_update().get(pk=user.pk)
        if identity_version is not None:
            locked.identity_version = max(locked.identity_version, identity_version)
        locked.display_name = user.display_name
        if login:  # вход заново активирует зеркало; refresh — нет (его не видит get_user)
            locked.is_active = True
        PermissionSync.apply(locked, roles=principal.roles, perms=principal.perms)
    # Объект запроса (request.user) должен видеть то же, что записано.
    user.refresh_from_db()


def _mirror(principal: Principal, data: IdentitySession) -> AdminUser:
    user, created = AdminUser.objects.get_or_create(
        identity_user_id=principal.user_id,
        defaults={"username": f"identity-{principal.user_id}"},
    )
    if created:
        user.set_unusable_password()  # своих паролей в админке нет
        user.save(update_fields=["password"])
    user.display_name = data.display_name or user.display_name
    _sync(user, principal, data.identity_version, login=True)
    return user


def client_ip(request: HttpRequest) -> str:
    """IP клиента админки: ``REMOTE_ADDR`` (``X-Forwarded-For`` уже учёл uvicorn)."""
    return str(request.META.get("REMOTE_ADDR") or "")


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
                {"login": username, "password": password, "client_ip": client_ip(request)},
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
        user = _mirror(principal, data)
        store_tokens(request.session, data)
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

    Refresh выполняется под блокировкой строки сессии (``SELECT … FOR UPDATE``): две вкладки
    с одной сессией не отправят один refresh-токен дважды (identity счёл бы это повтором
    украденного токена и отозвал сессию). Вторая вкладка дождётся первой и возьмёт уже
    обновлённые токены из БД.

    Raises:
        SessionExpiredError: токенов нет или identity отказал в refresh.
    """
    tokens = _tokens(request.session.get(SESSION_KEY))
    if tokens.fresh:
        return tokens.access
    session_key = request.session.session_key
    if not session_key:
        raise SessionExpiredError
    with transaction.atomic():
        row = Session.objects.select_for_update().filter(session_key=session_key).first()
        if row is None:
            raise SessionExpiredError
        current = row.get_decoded().get(SESSION_KEY)
        tokens = _tokens(current)
        if tokens.fresh:  # пока ждали блокировку, другая вкладка уже обновила токены
            request.session[SESSION_KEY] = current
            return tokens.access
        data = _refresh(request, tokens.refresh)
        store_tokens(request.session, data)
        # Записать в БД до снятия блокировки: ждущая вкладка должна увидеть новые токены.
        request.session.save()
    return data.access_token


def _refresh(request: HttpRequest, refresh_token: str) -> IdentitySession:
    from admin_service.core.middleware import request_id

    try:
        data = _identity_post(
            "/internal/v1/admin-sessions/refresh",
            {"refresh_token": refresh_token},
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
    user.display_name = data.display_name or user.display_name
    _sync(user, principal, data.identity_version, login=False)
    return data


def revoke_session(request: HttpRequest) -> None:
    """Отозвать admin-сессию в identity при выходе.

    Ошибка отзыва выход не отменяет: локальная сессия удаляется в любом случае, а refresh
    истечёт сам; отказ пишется в лог для разбора.
    """
    from admin_service.core.middleware import request_id

    try:
        tokens = _tokens(request.session.get(SESSION_KEY))
    except SessionExpiredError:
        return  # нечего отзывать (нет токенов или сменился ключ)
    try:
        response = _identity_call(
            "/internal/v1/admin-sessions/revoke",
            {"refresh_token": tokens.refresh},
            request_id(request),
        )
    except httpx.HTTPError as exc:
        log.warning("admin.revoke_failed", error=type(exc).__name__)
        return
    if response.is_error:
        log.warning("admin.revoke_failed", status=response.status_code)
        return
    log.info("admin.session_revoked")
