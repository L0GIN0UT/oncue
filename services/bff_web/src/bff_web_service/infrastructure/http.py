"""HTTP-вызовы сервисов-источников: таймаут, circuit breaker, проброс контекста, кэш.

Один ``UpstreamHttp`` на сервис-источник. Неудачи переводятся в два исключения портов:
``UpstreamUnavailableError`` (блок гаснет) и ``UpstreamRejectedError`` (осознанный 4xx).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

import httpx
import structlog
from prometheus_client import Counter, Histogram

from bff_web_service.application.ports import (
    TRACE_HEADERS,
    PageCache,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from gv_core.context import current_context

log = structlog.get_logger(__name__)

UPSTREAM_REQUESTS = Counter(
    "bff_upstream_requests_total",
    "Вызовы сервисов-источников по итогу",
    ["service", "outcome"],
)
UPSTREAM_DURATION = Histogram(
    "bff_upstream_request_duration_seconds",
    "Длительность вызова сервиса-источника",
    ["service"],
)
PAGE_CACHE = Counter("bff_page_cache_total", "Обращения к кэшу публичных блоков", ["result"])

_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERROR = 500
_HTTP_CLIENT_ERROR = 400


class BreakerState(StrEnum):
    """Состояние circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker одного сервиса-источника.

    ``failures`` неудач подряд открывают цепь: ``reset_s`` секунд вызовы не делаются вовсе
    (упавший сервис не держит страницы до таймаута и не добивается повторными запросами).
    Затем пропускается один пробный вызов: успех закрывает цепь, неудача открывает снова.
    """

    def __init__(
        self,
        *,
        failures: int,
        reset_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failures < 1 or reset_s <= 0:
            raise ValueError("failures >= 1, reset_s > 0")
        self._threshold, self._reset_s, self._clock = failures, reset_s, clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        """Текущее состояние (без побочных эффектов)."""
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self._reset_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        """Можно ли сделать вызов сейчас (в полуоткрытом состоянии — только один пробный)."""
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def success(self) -> None:
        """Вызов прошёл: цепь закрывается."""
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def failure(self) -> None:
        """Вызов не прошёл."""
        self._probe_in_flight = False
        if self._opened_at is not None:
            self._opened_at = self._clock()  # пробный вызов упал — ждём ещё одно окно
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()
            log.warning("bff.circuit_opened", failures=self._failures)

    def release(self) -> None:
        """Вызов завершился без вердикта о здоровье (4xx): снять флаг пробного вызова."""
        self._probe_in_flight = False


def outgoing_headers(token: str | None) -> dict[str, str]:
    """Заголовки вызова источника: Bearer субъекта, ``X-Request-Id``, контекст трассы."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    ctx = current_context()
    if ctx is not None:
        headers["X-Request-Id"] = str(ctx.correlation_id)
    headers.update(TRACE_HEADERS.get() or {})
    return headers


class UpstreamHttp:
    """GET-вызовы одного сервиса-источника.

    Args:
        service: Имя источника (метки метрик, тексты ошибок).
        client: httpx-клиент с ``base_url`` источника (жизненным циклом владеет wiring).
        breaker: Circuit breaker источника (один на процесс).
        timeout_s: Общий таймаут вызова, включая чтение тела.
        cache: Кэш публичных ответов (``None`` — без кэша).
    """

    def __init__(
        self,
        service: str,
        client: httpx.AsyncClient,
        breaker: CircuitBreaker,
        *,
        timeout_s: float,
        cache: PageCache | None = None,
    ) -> None:
        self.service = service
        self._client, self._breaker, self._timeout_s, self._cache = (
            client,
            breaker,
            timeout_s,
            cache,
        )

    async def get_public(self, path: str, *, cache_key: str) -> tuple[bytes, bool]:
        """Публичный ответ (без токена) через кэш: тело и признак «взято из кэша».

        В кэш тело кладёт вызывающий (``remember``) — только после разбора по контракту,
        чтобы мусорный ответ не закрепился на весь TTL.
        """
        if self._cache is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                PAGE_CACHE.labels("hit").inc()
                return cached, True
            PAGE_CACHE.labels("miss").inc()
        return await self.get(path, token=None), False

    async def remember(self, cache_key: str, body: bytes, ttl_s: int) -> None:
        """Положить в кэш тело, которое прошло разбор по контракту."""
        if self._cache is not None:
            await self._cache.set(cache_key, body, ttl_s)

    async def get(
        self, path: str, *, token: str | None, params: Mapping[str, str] | None = None
    ) -> bytes:
        """GET источника; тело успешного ответа в байтах.

        Raises:
            UpstreamUnavailableError: цепь разомкнута, таймаут, сеть, 5xx или 429.
            UpstreamRejectedError: прочие 4xx (код из Problem Details).
        """
        if not self._breaker.allow():
            UPSTREAM_REQUESTS.labels(self.service, "circuit_open").inc()
            raise UpstreamUnavailableError(self.service, "circuit_open")
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._client.get(
                    path, params=params, headers=outgoing_headers(token)
                )
        except TimeoutError as exc:
            raise self._unavailable("timeout") from exc
        except httpx.HTTPError as exc:
            raise self._unavailable(type(exc).__name__) from exc
        finally:
            UPSTREAM_DURATION.labels(self.service).observe(time.perf_counter() - started)
        status = response.status_code
        if status >= _HTTP_SERVER_ERROR or status == _HTTP_TOO_MANY:
            raise self._unavailable(f"http_{status}")
        if status >= _HTTP_CLIENT_ERROR:
            self._breaker.release()
            UPSTREAM_REQUESTS.labels(self.service, "rejected").inc()
            code, details = _problem(response)
            raise UpstreamRejectedError(self.service, status, code, details)
        self._breaker.success()
        UPSTREAM_REQUESTS.labels(self.service, "ok").inc()
        return response.content

    def _unavailable(self, reason: str) -> UpstreamUnavailableError:
        self._breaker.failure()
        UPSTREAM_REQUESTS.labels(self.service, "unavailable").inc()
        return UpstreamUnavailableError(self.service, reason)


def _problem(response: httpx.Response) -> tuple[str, dict[str, Any]]:
    """Код и ``details`` из Problem Details; тело не по контракту — общий код по статусу."""
    fallback = f"http.{response.status_code}"
    try:
        body = json.loads(response.content)
    except ValueError:
        return fallback, {}
    if not isinstance(body, dict):
        return fallback, {}
    code = body.get("code")
    details = body.get("details")
    return (
        code if isinstance(code, str) and code else fallback,
        details if isinstance(details, dict) else {},
    )
