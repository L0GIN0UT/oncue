"""Middleware админки: health-пробы, сквозной id запроса, allowlist IP."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Callable
from functools import cache

import structlog
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse

from gv_core.context import RequestContext, bind_context
from gv_core.ids import uuid7

log = structlog.get_logger(__name__)

Handler = Callable[[HttpRequest], HttpResponse]
REQUEST_ID_HEADER = "X-Request-Id"
HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})


def request_id(request: HttpRequest) -> str:
    """Сквозной id текущего запроса (передаётся сервисам в ``X-Request-Id``)."""
    return str(getattr(request, "gv_request_id", "")) or str(uuid7())


class HealthMiddleware:
    """``/health/live`` и ``/health/ready`` до проверки Host и allowlist.

    Пробы оркестратора приходят на IP пода (Host не из ``ALLOWED_HOSTS``) и не из сети
    админов; отвечают только статусом, без данных.
    """

    def __init__(self, get_response: Handler) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path not in HEALTH_PATHS:
            return self.get_response(request)
        if request.path == "/health/live":
            return JsonResponse({"status": "ok"})
        checks: dict[str, bool] = {}
        if connection.settings_dict["ENGINE"] != "django.db.backends.dummy":
            checks["postgres"] = _postgres_ok()
        ok = all(checks.values())
        return JsonResponse(
            {"status": "ok" if ok else "fail", "checks": checks}, status=200 if ok else 503
        )


def _postgres_ok() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        log.warning("health.postgres_failed", exc_info=True)
        return False
    return True


class RequestIdMiddleware:
    """Принимает корректный UUID из ``X-Request-Id`` или выдаёт свой; связывает с логами."""

    def __init__(self, get_response: Handler) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        rid = _parse(request.headers.get(REQUEST_ID_HEADER))
        request.gv_request_id = rid  # type: ignore[attr-defined]  # атрибут запроса админки
        with bind_context(RequestContext(correlation_id=rid)):
            response = self.get_response(request)
        response[REQUEST_ID_HEADER] = str(rid)
        return response


def _parse(raw: str | None) -> uuid.UUID:
    # Произвольную строку из заголовка в логи и аудит не пропускаем (log-injection).
    if raw:
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    return uuid7()


@cache
def _networks(raw: tuple[str, ...]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(n, strict=False) for n in raw)


def ip_allowed(remote_addr: str | None, allowlist: tuple[str, ...]) -> bool:
    """Адрес клиента входит в одну из сетей allowlist. Непарсящийся адрес — нет."""
    if not remote_addr:
        return False
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(address in network for network in _networks(allowlist))


class IpAllowlistMiddleware:
    """Доступ к админке только из сетей ``GV_ADMIN__IP_ALLOWLIST`` (офис, VPN, внутренняя сеть).

    Адрес клиента — ``REMOTE_ADDR``; ``X-Forwarded-For`` учитывает только uvicorn и только
    от доверенных прокси (``GV_ADMIN__FORWARDED_ALLOW_IPS``), иначе его подменил бы кто угодно.
    """

    def __init__(self, get_response: Handler) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        allowlist = tuple(settings.GV_IP_ALLOWLIST)
        if not ip_allowed(request.META.get("REMOTE_ADDR"), allowlist):
            log.warning("admin.ip_denied", path=request.path)
            return HttpResponseForbidden("доступ к админке только из разрешённых сетей")
        return self.get_response(request)
