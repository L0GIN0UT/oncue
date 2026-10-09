"""Устойчивые HTTP-вызовы к внешним источникам: rate-limit, ретраи, circuit breaker.

У каждого источника свой экземпляр ``ResilientHttp``: отказ неофициального клиента
Яндекс Музыки не задевает TMDB и наоборот (ADR 0014).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from gv_core.errors import DependencyUnavailableError, NotFoundError

log = structlog.get_logger(__name__)

Monotonic = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_S = 30.0
_HTTP_NOT_FOUND = 404


class TokenBucket:
    """Не больше ``rps`` запросов в секунду (с накоплением до одной секунды)."""

    def __init__(self, rps: float, *, monotonic: Monotonic = time.monotonic, sleep: Sleep) -> None:
        self._rate = rps
        self._capacity = max(1.0, rps)
        self._tokens = self._capacity
        self._monotonic = monotonic
        self._sleep = sleep
        self._updated = monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Дождаться права на запрос."""
        async with self._lock:
            while True:
                now = self._monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self._rate)


class CircuitBreaker:
    """После ``threshold`` отказов подряд — «открыт» на ``open_seconds``: вызовы сразу
    отклоняются, не нагружая лежащий источник. Затем одна пробная попытка."""

    def __init__(
        self, name: str, threshold: int, open_seconds: float, monotonic: Monotonic = time.monotonic
    ) -> None:
        self.name = name
        self._threshold = threshold
        self._open_seconds = open_seconds
        self._monotonic = monotonic
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Отклоняются ли вызовы сейчас."""
        if self._opened_at is None:
            return False
        return self._monotonic() - self._opened_at < self._open_seconds

    def check(self) -> None:
        """Бросить ``DependencyUnavailableError``, если цепь разомкнута."""
        if self.is_open:
            raise DependencyUnavailableError(
                f"источник {self.name} временно отключён", code="catalog.source_unavailable"
            )

    def success(self) -> None:
        """Успешный вызов замыкает цепь."""
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        """Учесть отказ."""
        self._failures += 1
        if self._failures >= self._threshold:
            if self._opened_at is None or not self.is_open:
                log.warning("catalog.source_circuit_opened", source=self.name)
            self._opened_at = self._monotonic()


class ResilientHttp:
    """httpx-клиент источника с лимитом частоты, ретраями 429/5xx и circuit breaker."""

    def __init__(
        self,
        name: str,
        client: httpx.AsyncClient,
        *,
        rps: float,
        breaker: CircuitBreaker,
        max_retries: int = 3,
        backoff_s: float = 0.5,
        sleep: Sleep = asyncio.sleep,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self.name = name
        self._client = client
        self._bucket = TokenBucket(rps, monotonic=monotonic, sleep=sleep)
        self._breaker = breaker
        self._max_retries = max_retries
        self._backoff = backoff_s
        self._sleep = sleep

    async def aclose(self) -> None:
        """Закрыть клиент."""
        await self._client.aclose()

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET и JSON. 404 → ``NotFoundError``; прочие отказы → ``DependencyUnavailableError``."""
        return (await self.request("GET", url, **kwargs)).json()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Запрос с ретраями. Возвращает только успешный (2xx) ответ."""
        self._breaker.check()
        attempt = 0
        while True:
            attempt += 1
            await self._bucket.acquire()
            delay: float | None = None
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                log.warning(
                    "catalog.source_transport_error", source=self.name, error=type(exc).__name__
                )
                delay = self._backoff * 2 ** (attempt - 1)
            else:
                if response.status_code == _HTTP_NOT_FOUND:
                    self._breaker.success()
                    raise NotFoundError(
                        "не найдено во внешнем источнике", code="catalog.external_not_found"
                    )
                if response.is_success:
                    self._breaker.success()
                    return response
                if response.status_code not in _RETRY_STATUSES:
                    # 400/401/403: ретрай не поможет (ключ, формат запроса) — отказ источника.
                    self._breaker.failure()
                    log.warning(
                        "catalog.source_rejected", source=self.name, status=response.status_code
                    )
                    raise DependencyUnavailableError(
                        f"источник {self.name} отклонил запрос", code="catalog.source_unavailable"
                    )
                delay = _retry_after(response) or self._backoff * 2 ** (attempt - 1)
            if attempt > self._max_retries:
                self._breaker.failure()
                raise DependencyUnavailableError(
                    f"источник {self.name} недоступен", code="catalog.source_unavailable"
                )
            await self._sleep(delay)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER_S)
    except ValueError:
        return None
