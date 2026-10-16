"""Лимиты частоты на Redis (§12 п. 4): фиксированное окно ``INCR`` + ``EXPIRE``.

* публикация — fail-closed: без Redis публиковать нельзя (иначе лимит «5 в сутки» снимается
  простым отказом Redis), ответ 503 ``collections.limiter_unavailable``;
* лайки, избранное, поиск — fail-open с метрикой: недоступный Redis не ломает каталог.
"""

from __future__ import annotations

import hashlib

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from collections_service.domain.errors import LimiterUnavailableError
from gv_core.clock import Clock

log = structlog.get_logger(__name__)

RATE_LIMITER_UNAVAILABLE = Counter(
    "collections_rate_limiter_unavailable_total",
    "Проверки лимита без Redis (fail-open — пропущены, fail-closed — отклонены)",
    ["bucket", "mode"],
)
RATE_LIMITED = Counter("collections_rate_limited_total", "Запросы, отклонённые лимитом", ["bucket"])

KEY_PREFIX = "collections:rl"


def _unavailable(bucket: str, *, fail_closed: bool) -> None:
    mode = "closed" if fail_closed else "open"
    RATE_LIMITER_UNAVAILABLE.labels(bucket, mode).inc()
    if fail_closed:
        raise LimiterUnavailableError()


class RedisRateLimiter:
    """``RateLimiter`` на Redis (субъект в ключе — sha256, без id в открытом виде)."""

    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(
        self, bucket: str, subject: str, *, limit: int, window_s: int, fail_closed: bool = False
    ) -> int | None:
        """Учесть запрос; превышение — секунды до нового окна."""
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
            log.warning(
                "collections.rate_limiter_unavailable",
                bucket=bucket,
                fail_closed=fail_closed,
                error_type=type(exc).__name__,
            )
            _unavailable(bucket, fail_closed=fail_closed)
            return None
        if int(count) > limit:
            RATE_LIMITED.labels(bucket).inc()
            return max((window + 1) * window_s - now, 1)
        return None


class UnconfiguredRateLimiter:
    """Redis не настроен (``GV_REDIS_URL`` пуст): как недоступный Redis."""

    async def hit(
        self,
        bucket: str,
        subject: str,  # noqa: ARG002 — сигнатура порта
        *,
        limit: int,  # noqa: ARG002
        window_s: int,  # noqa: ARG002
        fail_closed: bool = False,
    ) -> int | None:
        """Fail-open — пропустить, fail-closed — 503."""
        _unavailable(bucket, fail_closed=fail_closed)
        return None
