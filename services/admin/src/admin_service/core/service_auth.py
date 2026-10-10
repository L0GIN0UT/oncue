"""Сервисный токен admin для internal-ручек identity (D13 плана этапа 2, §2.8 п. 4).

``/internal/v1/admin-sessions*`` после шага 0b принимают только сервисный токен
(``require_service("admin_sessions")``, ``aud = identity``, ``sub = svc:admin``). Вызовы
идут из синхронного Django-кода, поэтому источник токена — синхронный: потокобезопасный
кэш с обновлением на 50 % TTL (``gv_auth.SyncServiceTokenProvider`` из 0b).

Точка подключения — ``build_token_source``: пока шаг 0b не влит, она возвращает ``None`` и
вызовы идут без сервисного токена, как на этапе 1. Отказ получения токена — это
недоступность identity (``ServiceError`` без статуса), а не «неверный пароль».
"""

from __future__ import annotations

import threading
from typing import Protocol

import structlog

from admin_service.core.service_client import ServiceError
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

    TODO(stage2/0b): после слияния шага 0b вернуть провайдер из общих настроек::

        from admin_service.django_settings import GV
        from gv_auth import SyncServiceTokenProvider

        auth = GV.service_auth
        if not auth.token_url or auth.client_secret is None:
            return None  # local/ci без секрета: identity сам решает, пускать ли
        return SyncServiceTokenProvider(
            auth.token_url,
            auth.client_id or GV.service_name,
            auth.client_secret.get_secret_value(),
            IDENTITY_AUDIENCE,
            timeout_s=auth.timeout_s,
            refresh_ratio=auth.refresh_ratio,
            retry_backoff_s=auth.retry_backoff_s,
        )
    """
    return None


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
