"""Кэш опубликованных конфигов режимов (``ModeConfigSource``).

В процессе, TTL ``GV_GAME__CONFIG_CACHE_TTL_S`` + поколение в Redis: после публикации
``INCR game:modes:gen``, каждый процесс сверяет поколение не чаще раза в секунду и при
смене сбрасывает «последние версии». Конкретные версии неизменяемы — кэшируются без срока.
Свой топик ``game.mode_config.v1`` game не читает.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from game_service.application.ports import UnitOfWorkFactory
from game_service.domain.modes import ConfigVersion

log = structlog.get_logger(__name__)

GENERATION_KEY = "game:modes:gen"
_GEN_CHECK_INTERVAL_S = 1.0


class CachedModeConfigs:
    """Конфиги режимов из БД с кэшем.

    Args:
        uow: Фабрика транзакций (чтение).
        redis: Redis для поколения (``None`` — только TTL).
        ttl_s: Срок кэша «последних версий».
        monotonic: Часы для TTL (тесты подменяют).
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        redis: Redis | None,
        *,
        ttl_s: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow = uow
        self._redis = redis
        self._ttl = ttl_s
        self._now = monotonic
        self._versions: dict[tuple[str, int], ConfigVersion] = {}
        self._latest: dict[str, tuple[float, ConfigVersion | None]] = {}
        self._published: tuple[float, list[ConfigVersion]] | None = None
        self._generation: bytes | None = None
        self._gen_checked_at = float("-inf")

    async def _check_generation(self) -> None:
        if self._redis is None or self._now() - self._gen_checked_at < _GEN_CHECK_INTERVAL_S:
            return
        self._gen_checked_at = self._now()
        try:
            raw = await self._redis.get(GENERATION_KEY)
            current = raw.encode() if isinstance(raw, str) else raw
        except (RedisError, OSError):
            log.warning("game.config_generation_unavailable")
            return
        if current != self._generation:
            self._generation = current
            self._drop()

    def _drop(self) -> None:
        self._latest.clear()
        self._published = None

    def _fresh(self, stored_at: float) -> bool:
        return self._now() - stored_at < self._ttl

    async def _cached[T](
        self, stored: tuple[float, T] | None, load: Callable[[], Awaitable[T]]
    ) -> tuple[float, T]:
        if stored is not None and self._fresh(stored[0]):
            return stored
        return self._now(), await load()

    async def latest(self, key: str) -> ConfigVersion | None:
        """Последняя версия режима."""
        await self._check_generation()

        async def load() -> ConfigVersion | None:
            async with self._uow() as uow:
                return (await uow.modes.get_line(key)).latest

        entry = await self._cached(self._latest.get(key), load)
        self._latest[key] = entry
        return entry[1]

    async def version(self, key: str, version: int) -> ConfigVersion | None:
        """Конкретная версия."""
        cached = self._versions.get((key, version))
        if cached is not None:
            return cached
        async with self._uow() as uow:
            found = await uow.modes.get_version(key, version)
        if found is not None:
            self._versions[(key, version)] = found
        return found

    async def published(self) -> list[ConfigVersion]:
        """Каталог режимов."""
        await self._check_generation()

        async def load() -> list[ConfigVersion]:
            async with self._uow() as uow:
                return await uow.modes.latest_published()

        self._published = await self._cached(self._published, load)
        return list(self._published[1])

    async def invalidate(self) -> None:
        """Сбросить кэш здесь и во всех процессах."""
        self._drop()
        if self._redis is None:
            return
        try:
            self._generation = str(await self._redis.incr(GENERATION_KEY)).encode()
        except (RedisError, OSError):
            # Остальные процессы обновятся по TTL.
            log.warning("game.config_generation_bump_failed")
