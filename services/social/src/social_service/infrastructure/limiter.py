"""Лимит попыток в Redis (fail-closed): комментарии в минуту."""

from __future__ import annotations

from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.errors import DependencyUnavailableError


class RedisRateLimiter:
    """Фиксированное окно: ``INCR`` + ``EXPIRE NX`` в MULTI.

    Недоступный Redis - 503 с ``details.retry_after``: ручка записи не должна работать без
    лимита (иначе обход спам-защиты остановкой Redis).
    """

    def __init__(
        self,
        redis: Callable[[], Redis | None],
        *,
        prefix: str = "social:rate:",
        retry_after_s: int = 5,
    ) -> None:
        self._get_redis = redis
        self._prefix = prefix
        self._retry_after_s = retry_after_s

    async def hit(self, subject: str, *, window_s: int) -> int:
        """Учесть попытку и вернуть её номер в окне."""
        redis = self._get_redis()
        if redis is None:
            raise self._unavailable()
        key = self._prefix + subject
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window_s, nx=True)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            raise self._unavailable() from exc
        return int(count)

    def _unavailable(self) -> DependencyUnavailableError:
        return DependencyUnavailableError(
            "лимитер недоступен", details={"retry_after": self._retry_after_s}
        )
