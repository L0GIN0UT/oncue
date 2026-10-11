"""Кэш публичных ответов источников в Redis.

Кэш — ускорение, а не источник истины: любая ошибка Redis гасится и ведёт себя как промах
(страница собирается напрямую из источников). Личные данные сюда не попадают — ключи
строят только клиенты публичных ручек.
"""

from __future__ import annotations

import asyncio

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)

PAGE_CACHE_ERRORS = Counter(
    "bff_page_cache_errors_total", "Ошибки Redis-кэша (запрос обслужен без кэша)", ["op"]
)


class RedisPageCache:
    """``GET``/``SET EX`` с гашением ошибок хранилища.

    Args:
        timeout_s: Предел одной операции: зависший Redis не должен съедать бюджет страницы
            (таймаут сокета клиента — секунды).
    """

    def __init__(self, client: Redis, *, timeout_s: float) -> None:
        self._r, self._timeout_s = client, timeout_s

    async def get(self, key: str) -> bytes | None:
        """Значение или ``None`` (в том числе при недоступном Redis)."""
        try:
            async with asyncio.timeout(self._timeout_s):
                value = await self._r.get(key)
        except (RedisError, TimeoutError) as exc:
            self._failed("get", exc)
            return None
        return value if isinstance(value, bytes) else None

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        """Сохранить на ``ttl_s`` секунд; ошибка Redis — только в лог и метрику."""
        try:
            async with asyncio.timeout(self._timeout_s):
                await self._r.set(key, value, ex=ttl_s)
        except (RedisError, TimeoutError) as exc:
            self._failed("set", exc)

    @staticmethod
    def _failed(op: str, exc: Exception) -> None:
        PAGE_CACHE_ERRORS.labels(op).inc()
        log.warning("bff.page_cache_unavailable", op=op, error=type(exc).__name__)
