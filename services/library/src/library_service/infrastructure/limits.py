"""Лимиты частоты на Redis (план этапа 3b §12 п. 4): фиксированное окно ``INCR`` + ``EXPIRE``.

Fail-open с метрикой: сохранение на полку не стоит отказа при недоступном Redis, а объём
медиатеки и так ограничен лимитами в БД (``items_per_shelf_max``, ``custom_shelves_max``).
"""

from __future__ import annotations

import hashlib

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.clock import Clock
from library_service.domain.errors import LibraryRateLimitedError

log = structlog.get_logger(__name__)

RATE_LIMITER_FAIL_OPEN = Counter(
    "library_rate_limiter_fail_open_total",
    "Запросы, пропущенные из-за недоступного Redis",
    ["bucket"],
)
RATE_LIMITED = Counter("library_rate_limited_total", "Запросы, отклонённые лимитом", ["bucket"])

KEY_PREFIX = "library:rl"


class RedisRateLimiter:
    """``RateLimiter`` на Redis; субъект в ключе — sha256 (id не лежит открытым текстом)."""

    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, bucket: str, subject: str, *, limit: int, window_s: int) -> None:
        """Учесть запрос; превышение — ``LibraryRateLimitedError``."""
        now = int(self._clock.now().timestamp())
        window = now // window_s
        digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
        key = f"{KEY_PREFIX}:{bucket}:{digest}:{window}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window_s + 1)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            RATE_LIMITER_FAIL_OPEN.labels(bucket).inc()
            log.warning(
                "library.rate_limiter_unavailable", bucket=bucket, error_type=type(exc).__name__
            )
            return
        if int(count) > limit:
            RATE_LIMITED.labels(bucket).inc()
            raise LibraryRateLimitedError(retry_after=(window + 1) * window_s - now)


class NoopRateLimiter:
    """Redis не настроен: лимиты не применяются (только local без Redis)."""

    async def hit(self, bucket: str, subject: str, *, limit: int, window_s: int) -> None:  # noqa: ARG002 — сигнатура порта
        """Ничего не делает (метрика fail-open растёт)."""
        RATE_LIMITER_FAIL_OPEN.labels(bucket).inc()
