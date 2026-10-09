"""RED-метрики HTTP (rate, errors, duration) для Prometheus.

Лейбл ``route`` — это шаблон пути (``/v1/games/{game_id}``), а не фактический путь:
иначе каждый идентификатор порождал бы новую временную серию и Prometheus деградировал бы.
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTTP_REQUESTS = Counter("gv_http_requests_total", "HTTP-запросы", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "gv_http_request_duration_seconds",
    "Длительность HTTP-запросов",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

_UNMATCHED = "__unmatched__"
_SKIP_ROUTES = frozenset({"/metrics", "/health/live", "/health/ready"})


def _route_template(scope: Scope) -> str:
    """Шаблон пути сработавшего маршрута.

    Роутер Starlette/FastAPI записывает найденный маршрут в ``scope["route"]`` (тот же
    словарь, что видит middleware), поэтому читаем его после обработки запроса.
    """
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return str(template) if template else _UNMATCHED


class MetricsMiddleware:
    """ASGI-middleware сбора RED-метрик."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] in _SKIP_ROUTES:
            await self.app(scope, receive, send)
            return

        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            method, route = scope["method"], _route_template(scope)
            HTTP_DURATION.labels(method, route).observe(time.perf_counter() - started)
            HTTP_REQUESTS.labels(method, route, str(status)).inc()


def render_metrics() -> tuple[bytes, str]:
    """Текущие метрики процесса в формате Prometheus: тело и Content-Type."""
    return generate_latest(), CONTENT_TYPE_LATEST


async def metrics_endpoint(_: Request) -> Response:
    """Отдать метрики в формате Prometheus."""
    body, content_type = render_metrics()
    return Response(body, media_type=content_type)
