"""Кэш проекции ladder-ов в процессе: ``tick`` очереди идёт каждые несколько секунд на игрока.

Конфиг ladder-а меняется редко (публикация версии), поэтому достаточно короткого TTL;
обработчик события сбрасывает кэш своего процесса, остальные процессы дозревают по TTL.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from uuid import UUID

from match_service.application.ports import LadderInfo, LadderProjection


class CachedLadders:
    """``LadderProjection`` с кэшем списка ladder-ов."""

    def __init__(
        self,
        inner: LadderProjection,
        ttl_s: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # Реальное время, а не Clock домена: иначе тестовые часы навсегда заморозят кэш.
        self._inner = inner
        self._monotonic = monotonic
        self._ttl_s = ttl_s
        self._items: list[LadderInfo] | None = None
        self._loaded_at = 0.0

    def invalidate(self) -> None:
        """Забыть кэш (пришёл ``LadderPublished`` / ``SeasonStarted``)."""
        self._items = None

    async def all(self) -> list[LadderInfo]:
        """Все ladder-ы."""
        now = self._monotonic()
        if self._items is None or now - self._loaded_at >= self._ttl_s:
            self._items = await self._inner.all()
            self._loaded_at = now
        return self._items

    async def ladder(self, key: str) -> LadderInfo | None:
        """Ladder по ключу."""
        return next((item for item in await self.all() if item.ladder == key), None)

    async def rating(self, user_id: UUID, ladder: str) -> int | None:
        """Рейтинг игрока (без кэша: меняется после каждого матча)."""
        return await self._inner.rating(user_id, ladder)
