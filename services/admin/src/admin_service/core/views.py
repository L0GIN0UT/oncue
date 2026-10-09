"""Общие помощники представлений: клиент сервиса и мутация с аудитом."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import HttpRequest
from django.urls import reverse

from admin_service.audit.services import record
from admin_service.core.auth import access_token
from admin_service.core.middleware import request_id
from admin_service.core.models import AdminUser
from admin_service.core.service_client import ServiceClient, ServiceError

log = structlog.get_logger(__name__)


def service_client(request: HttpRequest, service: str) -> ServiceClient:
    """Клиент сервиса с токеном вошедшего пользователя и id текущего запроса."""
    base_url = settings.GV_SERVICE_URLS.get(service)
    if not base_url:
        raise ImproperlyConfigured(f"не задан GV_ADMIN__SERVICE_URLS__{service}")
    return ServiceClient(
        service,
        base_url=base_url,
        access_token=access_token(request),
        request_id=request_id(request),
        timeout_s=settings.GV_SERVICE_TIMEOUT_S,
    )


def fetch(request: HttpRequest, service: str, path: str, **params: Any) -> tuple[Any, str | None]:
    """GET к сервису: ``(данные, None)`` или ``(None, сообщение об ошибке)``.

    Недоступность одного сервиса не роняет страницу: раздел показывает ошибку.
    """
    try:
        with service_client(request, service) as client:
            return client.get(path, params=params), None
    except ServiceError as exc:
        return None, exc.message


def mutate(
    request: HttpRequest,
    *,
    service: str,
    action: str,
    target_type: str,
    target_id: str,
    reason: str,
    idempotency_key: str,
    call: Callable[[ServiceClient], Any],
    before: Any = None,
    audit_after: Any = None,
    success: str = "Готово",
) -> tuple[bool, Any]:
    """Выполнить действие через admin-API и записать аудит (и при успехе, и при отказе).

    Returns:
        ``(успех, ответ сервиса)``; сообщение пользователю уже добавлено.

    ``audit_after`` — что записать в аудит как «после», если ответ сервиса для этого
    не годится (например, при загрузке файла).
    """
    user = request.user
    if not isinstance(user, AdminUser):  # require_perm гарантирует вход; защита от ошибки маршрута
        raise PermissionDenied
    common = {
        "actor": user,
        "action": action,
        "service": service,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
        "request_id": request_id(request),
        "idempotency_key": idempotency_key,
        "before": before,
    }
    try:
        with service_client(request, service) as client:
            result = call(client)
    except ServiceError as exc:
        record(**common, error_code=exc.code)
        messages.error(request, exc.message)
        return False, None
    record(**common, after=result if audit_after is None else audit_after)
    log.info("admin.action", action=action, target_id=target_id)
    messages.success(request, success)
    return True, result


def with_links(rows: Any, route: str, kwarg: str, *id_keys: str) -> list[dict[str, Any]]:
    """Строки ответа сервиса + ссылка на карточку (``admin_url``), если id — корректный UUID.

    Ссылку строим здесь, а не в шаблоне: неожиданный id из ответа не должен ронять страницу.
    """
    result: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        raw = next((row[k] for k in id_keys if row.get(k)), None)
        try:
            url = reverse(route, kwargs={kwarg: UUID(str(raw))}) if raw else None
        except ValueError:
            url = None
        result.append({**row, "admin_url": url})
    return result
