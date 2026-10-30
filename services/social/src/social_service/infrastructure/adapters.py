"""Redis-адаптеры social: лимитер попыток и присутствие (ADR 0050)."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.clock import Clock
from social_service.application.ports.friends import AttemptWindow
from social_service.domain.friends import LimiterUnavailableError

log = structlog.get_logger(__name__)

LIMITER_PREFIX = "social:limit:"
PRESENCE_PREFIX = "social:presence:"
PRESENCE_ONLINE_KEY = "social:presence-online"
"""ZSET счётчика онлайна: член - ``user_id``, оценка - момент истечения (unix, секунды)."""


class RedisAttemptLimiter:
    """Окно фиксированной длины: ``INCR`` + ``EXPIRE NX`` + ``TTL`` атомарно в MULTI.

    Отказ Redis - fail-closed: 503 ``social.limiter_unavailable`` с ``Retry-After``.
    """

    def __init__(self, redis: Redis, *, retry_after_s: int) -> None:
        self._redis = redis
        self._retry_after_s = retry_after_s

    async def hit(self, key: str, window_s: int) -> AttemptWindow:
        """Учесть попытку."""
        full = f"{LIMITER_PREFIX}{key}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(full)
                pipe.expire(full, window_s, nx=True)
                pipe.ttl(full)
                count, _, ttl = await pipe.execute()
        except RedisError as exc:
            raise LimiterUnavailableError(
                "лимитер недоступен", details={"retry_after": self._retry_after_s}
            ) from exc
        return AttemptWindow(count=int(count), resets_in_s=max(int(ttl), 1))


class UnconfiguredLimiter:
    """Нет Redis (``GV_REDIS_URL``): операции с лимитом закрыты (fail-closed)."""

    def __init__(self, retry_after_s: int) -> None:
        self._retry_after_s = retry_after_s

    async def hit(self, key: str, window_s: int) -> AttemptWindow:  # noqa: ARG002 - порт
        """Всегда отказ."""
        raise LimiterUnavailableError(
            "лимитер не настроен (GV_REDIS_URL)", details={"retry_after": self._retry_after_s}
        )


class RedisPresenceStore:
    """Присутствие: ключ ``social:presence:<user_id>`` с TTL."""

    def __init__(self, redis: Redis, clock: Clock, *, retry_after_s: int) -> None:
        self._redis = redis
        self._clock = clock
        self._retry_after_s = retry_after_s

    async def touch(self, user_id: UUID, ttl_s: int) -> None:
        """Отметить игрока онлайн; недоступность Redis - 503.

        Ключ игрока и запись счётчика (ZSET, оценка - момент истечения) обновляются одной
        транзакцией; сам ZSET тоже получает TTL, чтобы не жить вечно без heartbeat.
        """
        expires_at = self._clock.now().timestamp() + ttl_s
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(f"{PRESENCE_PREFIX}{user_id}", "1", ex=ttl_s)
                pipe.zadd(PRESENCE_ONLINE_KEY, {str(user_id): expires_at})
                pipe.expire(PRESENCE_ONLINE_KEY, ttl_s)
                await pipe.execute()
        except RedisError as exc:
            raise LimiterUnavailableError(
                "присутствие недоступно", details={"retry_after": self._retry_after_s}
            ) from exc

    async def online(self, user_ids: Collection[UUID]) -> set[UUID]:
        """Кто онлайн; недоступность Redis - деградация до «никто» (список друзей работает)."""
        ids = list(user_ids)
        if not ids:
            return set()
        try:
            values = await self._redis.mget([f"{PRESENCE_PREFIX}{u}" for u in ids])
        except RedisError:
            log.warning("social.presence_unavailable")
            return set()
        return {u for u, v in zip(ids, values, strict=True) if v is not None}

    async def count_online(self) -> int:
        """Игроки с неистёкшей записью: ``ZREMRANGEBYSCORE`` по текущему времени и ``ZCARD``."""
        now = self._clock.now().timestamp()
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(PRESENCE_ONLINE_KEY, "-inf", now)
                pipe.zcard(PRESENCE_ONLINE_KEY)
                _, count = await pipe.execute()
        except RedisError as exc:
            raise LimiterUnavailableError(
                "счётчик онлайна недоступен", details={"retry_after": self._retry_after_s}
            ) from exc
        return int(count)


class UnconfiguredPresence:
    """Нет Redis: присутствие недоступно, статус друзей - offline/in_match."""

    def __init__(self, retry_after_s: int) -> None:
        self._retry_after_s = retry_after_s

    async def touch(self, user_id: UUID, ttl_s: int) -> None:  # noqa: ARG002 - порт
        """Всегда отказ."""
        raise LimiterUnavailableError(
            "присутствие не настроено (GV_REDIS_URL)", details={"retry_after": self._retry_after_s}
        )

    async def online(self, user_ids: Collection[UUID]) -> set[UUID]:  # noqa: ARG002 - порт
        """Никто."""
        return set()

    async def count_online(self) -> int:
        """Счётчик недоступен."""
        raise LimiterUnavailableError(
            "присутствие не настроено (GV_REDIS_URL)", details={"retry_after": self._retry_after_s}
        )
