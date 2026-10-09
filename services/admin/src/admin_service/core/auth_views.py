"""Вход и выход админки поверх ``admin.site``: лимит попыток по IP и отзыв сессии identity.

Лимит попыток здесь — дополнительно к лимиту identity: identity считает попытки по логину
и своему адресу клиента, админка — по IP модератора ещё до обращения к identity (перебор
паролей с одного адреса не доходит до identity вовсе). Окно фиксированное: счётчик
создаётся первой попыткой и живёт ``GV_ADMIN__LOGIN_ATTEMPTS_WINDOW_S``.
"""

from __future__ import annotations

import contextlib

import structlog
from django.conf import settings
from django.contrib import admin
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse

from admin_service.core.auth import client_ip, revoke_session

log = structlog.get_logger(__name__)

ATTEMPTS_KEY = "login_attempts:{ip}"
_FOUND = 302


def _attempts_key(request: HttpRequest) -> str:
    return ATTEMPTS_KEY.format(ip=client_ip(request) or "unknown")


def _count_attempt(key: str, window_s: int) -> int:
    """Атомарно учесть попытку; возвращает номер попытки в текущем окне."""
    cache.add(key, 0, timeout=window_s)  # no-op, если окно уже открыто
    try:
        return int(cache.incr(key))
    except ValueError:  # окно истекло между add и incr
        cache.set(key, 1, timeout=window_s)
        return 1


def _release_attempt(key: str) -> None:
    """Успешный вход не расходует лимит (офис за одним NAT не блокирует сам себя)."""
    with contextlib.suppress(ValueError):  # окно уже истекло
        cache.decr(key)


def login(request: HttpRequest) -> HttpResponse:
    """Форма входа ``admin.site.login`` с лимитом попыток по IP клиента."""
    if request.method != "POST":
        return admin.site.login(request)
    key = _attempts_key(request)
    window_s = int(settings.GV_LOGIN_ATTEMPTS_WINDOW_S)
    attempt = _count_attempt(key, window_s)
    if attempt > int(settings.GV_LOGIN_ATTEMPTS_LIMIT):
        log.warning("admin.login_throttled", attempt=attempt)
        return HttpResponse(
            "Слишком много попыток входа — повторите позже",
            status=429,
            content_type="text/plain; charset=utf-8",
            headers={"Retry-After": str(window_s)},
        )
    response = admin.site.login(request)
    if response.status_code == _FOUND and request.user.is_authenticated:
        _release_attempt(key)
    return response


def logout(request: HttpRequest) -> HttpResponse:
    """Выход: отозвать admin-сессию в identity, затем ``admin.site.logout``.

    Отзыв — только на POST (выход в Django 5 — POST с CSRF; GET получит 405 от
    ``LogoutView`` и ничего не отзовёт). Ошибка отзыва выход не отменяет.
    """
    if request.method == "POST" and request.user.is_authenticated:
        revoke_session(request)
    return admin.site.logout(request)
