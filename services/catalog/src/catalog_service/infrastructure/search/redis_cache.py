"""Кэш префиксов автокомплита и rate-limit в Redis.

Инвалидация кэша — через «поколение»: ключ ответа содержит номер поколения, а
``invalidate`` увеличивает его (``INCR``). Старые ключи просто истекают по TTL; ``KEYS``/
``SCAN`` с удалением не нужны. Ошибки Redis не роняют запрос: кэш — оптимизация,
rate-limit при недоступном Redis работает fail-open (с предупреждением в логе).
"""

from __future__ import annotations

import json

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from catalog_service.application.dto import Suggestion
from gv_core.clock import Clock

log = structlog.get_logger(__name__)


class RedisPrefixCache:
    """``PrefixCache`` на Redis."""

    def __init__(self, redis: Redis, *, prefix: str, ttl_s: int) -> None:
        self._r = redis
        self._prefix = f"{prefix}:ac"
        self._ttl = ttl_s

    @property
    def _gen_key(self) -> str:
        return f"{self._prefix}:gen"

    async def _generation(self) -> str:
        value = await self._r.get(self._gen_key)
        return value.decode() if isinstance(value, bytes) else str(value or 0)

    async def get(self, key: str) -> list[Suggestion] | None:
        """Ответ из кэша."""
        try:
            raw = await self._r.get(f"{self._prefix}:{await self._generation()}:{key}")
        except RedisError:
            log.warning("catalog.prefix_cache_unavailable", op="get")
            return None
        if raw is None:
            return None
        try:
            return [Suggestion.from_json(item) for item in json.loads(raw)]
        except (ValueError, KeyError, TypeError):
            log.warning("catalog.prefix_cache_corrupted")
            return None

    async def set(self, key: str, value: list[Suggestion]) -> None:
        """Положить ответ."""
        payload = json.dumps([s.to_json() for s in value], ensure_ascii=False)
        try:
            await self._r.set(
                f"{self._prefix}:{await self._generation()}:{key}", payload, ex=self._ttl
            )
        except RedisError:
            log.warning("catalog.prefix_cache_unavailable", op="set")

    async def invalidate(self) -> None:
        """Новое поколение — все прежние ответы становятся недоступны."""
        try:
            await self._r.incr(self._gen_key)
        except RedisError:
            # Без инвалидации заблокированная единица проживёт в кэше до TTL — логируем громко.
            log.error("catalog.prefix_cache_invalidate_failed")  # noqa: TRY400


class RedisRateLimiter:
    """Фиксированное окно в минуту: ``INCR`` + ``EXPIRE`` на ключ клиента."""

    def __init__(self, redis: Redis, clock: Clock, *, prefix: str, limit_per_minute: int) -> None:
        self._r = redis
        self._clock = clock
        self._prefix = f"{prefix}:rl"
        self._limit = limit_per_minute

    async def hit(self, key: str) -> bool:
        """Учесть запрос; ``False`` — лимит окна исчерпан."""
        window = int(self._clock.now().timestamp()) // 60
        redis_key = f"{self._prefix}:{key}:{window}"
        try:
            async with self._r.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, 61)
                count, _ = await pipe.execute()
        except RedisError:
            log.warning("catalog.rate_limit_unavailable")
            return True
        return int(count) <= self._limit
