"""Лимиты частоты на Redis (§9 п. 4): фиксированное окно ``INCR`` + ``EXPIRE``.

Fail-open с метрикой: попытки и так ограничены правилами раунда, а выдачу ссылок на
ассеты дополнительно защищает fail-closed лимитер media-pipeline.
"""

from __future__ import annotations

import hashlib

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from game_service.domain.errors import GameRateLimitedError
from gv_core.clock import Clock

log = structlog.get_logger(__name__)

RATE_LIMITER_FAIL_OPEN = Counter(
    "game_rate_limiter_fail_open_total", "Запросы, пропущенные из-за недоступного Redis", ["bucket"]
)
RATE_LIMITED = Counter("game_rate_limited_total", "Запросы, отклонённые лимитом", ["bucket"])

KEY_PREFIX = "game:rl"


class RedisRateLimiter:
    """``RateLimiter`` на Redis.

    Субъект в ключе — sha256 (IP гостя и id не лежат в Redis открытым текстом).
    """

    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, bucket: str, subject: str, *, limit: int, window_s: int) -> None:
        """Учесть запрос; превышение — ``GameRateLimitedError``."""
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
                "game.rate_limiter_unavailable", bucket=bucket, error_type=type(exc).__name__
            )
            return
        if int(count) > limit:
            RATE_LIMITED.labels(bucket).inc()
            raise GameRateLimitedError(retry_after=(window + 1) * window_s - now)


class NoopRateLimiter:
    """Redis не настроен: лимиты не применяются (только local без Redis)."""

    async def hit(self, bucket: str, subject: str, *, limit: int, window_s: int) -> None:  # noqa: ARG002 — сигнатура порта
        """Ничего не делает."""
        RATE_LIMITER_FAIL_OPEN.labels(bucket).inc()
