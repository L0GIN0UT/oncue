"""Лимит попыток отписки по IP в Redis (fail-closed)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.errors import DependencyUnavailableError


class RedisAttemptLimiter:
    """Фиксированное окно: ``INCR`` + ``EXPIRE NX`` в MULTI.

    Отказ Redis - 503 с ``details.retry_after`` (заголовок ``Retry-After``): ручка без входа
    не должна работать без лимита. Адрес клиента в ключе - хешем (ПДн в Redis не храним).
    """

    def __init__(
        self,
        redis: Callable[[], Redis | None],
        *,
        window_s: int,
        retry_after_s: int,
        prefix: str = "notifications:unsub:",
    ) -> None:
        self._get_redis = redis
        self._window_s = window_s
        self._retry_after_s = retry_after_s
        self._prefix = prefix

    async def hit(self, subject: str) -> int:
        """Учесть попытку и вернуть её номер в окне."""
        redis = self._get_redis()
        if redis is None:
            raise self._unavailable()
        key = self._prefix + hashlib.sha256(subject.encode()).hexdigest()[:32]
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, self._window_s, nx=True)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            raise self._unavailable() from exc
        return int(count)

    def _unavailable(self) -> DependencyUnavailableError:
        return DependencyUnavailableError(
            "лимитер попыток недоступен", details={"retry_after": self._retry_after_s}
        )
