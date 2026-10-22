"""Лимиты частоты на Redis (§12 п. 4): фиксированное окно ``INCR`` + ``EXPIRE``.

Субъект в ключе — sha256 (IP и id не лежат в Redis открытым текстом). По умолчанию
fail-open с метрикой (команды ещё ограничены правилами раунда), перебор кодов комнат —
fail-closed: без Redis лимит не проверить, а перебор дешёвый.
"""

from __future__ import annotations

import hashlib

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.clock import Clock
from match_service.domain.errors import MatchRateLimitedError, MatchUnavailableError

log = structlog.get_logger(__name__)

LIMITER_UNAVAILABLE = Counter(
    "gv_match_rate_limiter_unavailable_total", "Проверки лимита без Redis", ["bucket", "mode"]
)
RATE_LIMITED = Counter("gv_match_rate_limited_total", "Отказы по лимиту", ["bucket"])

KEY_PREFIX = "match:rl"


class RedisRateLimiter:
    """``RateLimiter`` на Redis."""

    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(
        self, bucket: str, subject: str, *, limit: int, window_s: int, fail_closed: bool = False
    ) -> None:
        """Учесть запрос."""
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
            mode = "closed" if fail_closed else "open"
            LIMITER_UNAVAILABLE.labels(bucket, mode).inc()
            log.warning("match.rate_limiter_unavailable", bucket=bucket, mode=mode)
            if fail_closed:
                raise MatchUnavailableError("проверка лимита недоступна") from exc
            return
        if int(count) > limit:
            RATE_LIMITED.labels(bucket).inc()
            raise MatchRateLimitedError(retry_after=(window + 1) * window_s - now)
