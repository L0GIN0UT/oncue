"""Сервисный токен admin для internal-ручек identity (D13 плана этапа 2, §2.8 п. 4).

``/internal/v1/admin-sessions*`` после шага 0b принимают только сервисный токен
(``require_service("admin_sessions")``, ``aud = identity``, ``sub = svc:admin``). Вызовы
идут из синхронного Django-кода, поэтому источник токена — синхронный: потокобезопасный
кэш с обновлением на 50 % TTL (``gv_auth.SyncServiceTokenProvider`` из 0b).

Точка подключения — ``build_token_source`` (провайдер из ``GV_SERVICE_AUTH__*``). Отказ
получения токена — это недоступность identity (``ServiceError`` без статуса), а не
«неверный пароль».
"""

from __future__ import annotations

import threading
from typing import Protocol

import structlog

from admin_service.core.service_client import ServiceError
from gv_auth import SyncServiceTokenProvider
from gv_core.errors import AppError

log = structlog.get_logger(__name__)

IDENTITY_AUDIENCE = "identity"
"""``aud`` сервисного токена для identity (имя вызываемого сервиса)."""


class TokenSource(Protocol):
    """Источник заголовка ``Authorization`` для исходящего вызова (кэширует токен сам)."""

    def headers(self) -> dict[str, str]:
        """Заголовки с действующим токеном.

        Raises:
            gv_core.errors.AppError: identity не выдал токен и живого в кэше нет.
        """
        ...


def build_token_source() -> TokenSource | None:
    """Источник сервисного токена admin → identity; ``None`` — сервисная аутентификация выключена.

    Учётные данные — общие ``GV_SERVICE_AUTH__*`` (``ServiceAuthSettings``). Без адреса
    identity или секрета (local/ci без ``gv dev secrets``) вызовы идут без токена, и identity
    сам решает, пускать ли (после шага 0b — 401).
    """
    from admin_service.django_settings import GV

    auth = GV.service_auth
    if not auth.token_url or auth.client_secret is None:
        log.warning("admin.service_auth_not_configured")
        return None
    return SyncServiceTokenProvider(
        auth.token_url,
        auth.client_id or GV.service_name,
        auth.client_secret.get_secret_value(),
        IDENTITY_AUDIENCE,
        timeout_s=auth.timeout_s,
        refresh_ratio=auth.refresh_ratio,
        retry_backoff_s=auth.retry_backoff_s,
    )


_lock = threading.Lock()
_state: dict[str, TokenSource | None] = {}


def token_source() -> TokenSource | None:
    """Источник токена процесса (создаётся один раз: кэш токена общий для всех потоков)."""
    if "source" not in _state:
        with _lock:
            if "source" not in _state:
                _state["source"] = build_token_source()
    return _state["source"]


def identity_headers() -> dict[str, str]:
    """Заголовки сервисной аутентификации для вызова internal-ручки identity.

    Raises:
        ServiceError: токен получить не удалось (identity недоступен) — без статуса, как
            сетевой отказ: форма входа покажет «вход временно невозможен».
    """
    source = token_source()
    if source is None:
        return {}
    try:
        return source.headers()
    except AppError as exc:
        # Ни токена, ни секрета в логе: только код ошибки.
        log.warning("admin.service_token_unavailable", code=exc.code)
        raise ServiceError(
            "identity", None, "service.unavailable", "identity недоступен (сервисный токен)"
        ) from exc
