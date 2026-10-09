"""Защита от CSRF для запросов с cookie-аутентификацией (docs/plans/stage-1.md §8 п.3).

Модель — double submit cookie плюс проверка Origin:

* мутация (не GET/HEAD/OPTIONS) с access-токеном из cookie проходит, только если
  ``Origin`` (или, без него, origin из ``Referer``) входит в ``app.state.csrf_origins``
  **и** заголовок ``X-CSRF-Token`` совпадает с cookie ``gv_csrf``;
* запросы с ``Authorization: Bearer`` не проверяются: браузер не подставляет заголовок сам;
* ``require_same_origin`` — для входа и регистрации (login-CSRF): если браузер прислал
  Origin/Referer, он обязан быть разрешённым; без обоих заголовков запрос не из браузера.

Пустой список разрешённых Origin — отказ (fail-closed): забытая настройка не должна
отключать защиту.
"""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import Request

from gv_core.errors import ForbiddenError

CSRF_COOKIE = "gv_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TOKEN_BYTES = 32


class CsrfError(ForbiddenError):
    """Запрос не прошёл проверку CSRF/Origin."""

    default_code = "auth.csrf_failed"


def new_csrf_token() -> str:
    """Новый CSRF-токен (32 байта из ``secrets``, base64url)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def normalize_origin(value: str) -> str | None:
    """Origin вида ``scheme://host[:port]`` в нижнем регистре; ``None`` — не origin."""
    raw = value.strip()
    if raw.lower() == "null":
        return "null"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    return f"{parts.scheme.lower()}://{host}" + (f":{port}" if port else "")


def request_origin(request: Request) -> str | None:
    """Origin запроса: заголовок ``Origin``, а без него — origin из ``Referer``."""
    origin = request.headers.get("origin")
    if origin is not None:
        return normalize_origin(origin) or "invalid"
    referer = request.headers.get("referer")
    if referer:
        return normalize_origin(referer) or "invalid"
    return None


def allowed_origins(request: Request) -> frozenset[str]:
    """Разрешённые Origin из ``app.state.csrf_origins`` (нормализованные)."""
    raw = getattr(request.app.state, "csrf_origins", None) or ()
    return frozenset(o for o in (normalize_origin(str(v)) for v in raw) if o and o != "null")


def check_origin(request: Request, *, required: bool) -> None:
    """Проверить Origin/Referer.

    Args:
        required: ``True`` — отсутствие обоих заголовков тоже ошибка.

    Raises:
        CsrfError: Origin чужой, ``null`` или отсутствует при ``required``.
    """
    origin = request_origin(request)
    if origin is None:
        if required:
            raise CsrfError("не удалось проверить источник запроса")
        return
    if origin not in allowed_origins(request):
        raise CsrfError("запрос с недопустимого источника")


def check_csrf_token(request: Request) -> None:
    """Заголовок ``X-CSRF-Token`` совпадает с cookie ``gv_csrf`` (сравнение за константное время).

    Raises:
        CsrfError: заголовка или cookie нет, либо они различаются.
    """
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    header = request.headers.get(CSRF_HEADER) or ""
    if not cookie or not header or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise CsrfError("неверный CSRF-токен")


def enforce_csrf(request: Request) -> None:
    """Полная проверка запроса с cookie-аутентификацией (безопасные методы не проверяются)."""
    if request.method.upper() in SAFE_METHODS:
        return
    check_origin(request, required=True)
    check_csrf_token(request)


async def require_same_origin(request: Request) -> None:
    """Зависимость FastAPI для входа и регистрации: защита от login-CSRF."""
    check_origin(request, required=False)


async def require_csrf(request: Request) -> None:
    """Зависимость FastAPI для мутаций, аутентифицируемых cookie без access-токена (refresh)."""
    enforce_csrf(request)
