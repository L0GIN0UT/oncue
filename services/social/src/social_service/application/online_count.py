"""Публичный счётчик «игроков онлайн» (Redis, presence).

Считаются только игроки с аккаунтом: heartbeat гостю закрыт. Число не персональное, но ручка
публичная: чтение закрыто лимитом по IP (``GV_SOCIAL__ONLINE_COUNT_PER_MINUTE``) и короткой
копией в процессе (``GV_SOCIAL__ONLINE_COUNT_CACHE_S``) — Redis не слышит каждый запрос.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gv_core.clock import Clock
from social_service.application.ports.friends import AttemptLimiter, PresenceStore
from social_service.domain.friends import OnlineCountRateLimitedError

ONLINE_COUNT_WINDOW_S = 60
"""Окно лимита чтения счётчика."""


class OnlineCounter:
    """Число игроков онлайн с короткой копией и лимитом по IP."""

    def __init__(
        self,
        presence: PresenceStore,
        limiter: AttemptLimiter,
        clock: Clock,
        *,
        cache_s: float,
        per_minute: int,
    ) -> None:
        self._presence = presence
        self._limiter = limiter
        self._clock = clock
        self._cache = timedelta(seconds=cache_s)
        self.cache_s = int(cache_s)
        """Срок копии, с (для ``Cache-Control`` публичной ручки)."""
        self._per_minute = per_minute
        self._value: int | None = None
        self._fresh_until: datetime | None = None

    async def count(self, client_ip: str | None) -> int:
        """Сколько игроков онлайн.

        Raises:
            OnlineCountRateLimitedError: больше ``per_minute`` чтений в минуту с адреса.
            LimiterUnavailableError: Redis недоступен (fail-closed, 503).
        """
        window = await self._limiter.hit(
            f"online-count:{client_ip or 'unknown'}", ONLINE_COUNT_WINDOW_S
        )
        if window.count > self._per_minute:
            raise OnlineCountRateLimitedError(
                "слишком частые запросы счётчика онлайна",
                details={"retry_after": window.resets_in_s},
            )
        now = self._clock.now()
        if self._value is not None and self._fresh_until is not None and now < self._fresh_until:
            return self._value
        self._value = await self._presence.count_online()
        self._fresh_until = now + self._cache
        return self._value
