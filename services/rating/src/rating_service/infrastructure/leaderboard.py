"""Лидерборды в Redis ZSET ``{lb:<ladder>:<season_id>}`` (ADR 0039).

Redis — хранилище чтения: источник истины Postgres, пересборка — ``replace``. Метка «готов»
(``<ключ>:ready``) отличает пустой лидерборд от потерянного; у активных сезонов она с TTL,
поэтому лидерборд периодически пересобирается и сам (self-heal после сбоя записи).
Ключи несут hash-tag ``{...}``: ZSET, хэш версий, временные ключи и метки — в одном слоте.

Запись игрока версионирована (``<ключ>:ver`` — версия ``RatingChanged`` по игроку): запоздавшая
запись со старой версией не затирает новую, а «надгробие» удалённого/выбывшего игрока не даёт
старой записи вернуть его на доску. Проверка и запись — один Lua-скрипт (атомарно).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.errors import DependencyUnavailableError

_CHUNK = 5000

# KEYS: 1 — ZSET, 2 — хэш версий; ARGV: 1 — член, 2 — оценка (или "" — удалить), 3 — версия.
_WRITE_IF_NEWER = """
local cur = redis.call('HGET', KEYS[2], ARGV[1])
if cur and tonumber(cur) >= tonumber(ARGV[3]) then return 0 end
if ARGV[2] == '' then
  redis.call('ZREM', KEYS[1], ARGV[1])
else
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
end
redis.call('HSET', KEYS[2], ARGV[1], ARGV[3])
return 1
"""


def board_key(ladder: str, season_id: UUID) -> str:
    """Ключ ZSET лидерборда сезона."""
    return f"{{lb:{ladder}:{season_id}}}"


class RedisLeaderboard:
    """``LeaderboardStore`` на Redis; ошибки Redis → ``DependencyUnavailableError``."""

    def __init__(self, redis: Redis | None, *, ready_ttl_s: int | None) -> None:
        self._redis = redis
        self._ready_ttl_s = ready_ttl_s

    @asynccontextmanager
    async def _guard(self) -> AsyncIterator[Redis]:
        if self._redis is None:
            raise DependencyUnavailableError("Redis не настроен (GV_REDIS_URL)")
        try:
            yield self._redis
        except (RedisError, OSError) as exc:
            raise DependencyUnavailableError("Redis недоступен") from exc

    async def _write(
        self, ladder: str, season_id: UUID, user_id: UUID, score: int | None, version: int
    ) -> None:
        key = board_key(ladder, season_id)
        async with self._guard() as redis:
            await redis.eval(
                _WRITE_IF_NEWER,
                2,
                key,
                f"{key}:ver",
                str(user_id),
                "" if score is None else str(score),
                str(version),
            )

    async def put(
        self, ladder: str, season_id: UUID, user_id: UUID, rating: int, version: int
    ) -> None:
        """Поставить игрока (или обновить рейтинг), если версия новее записанной."""
        await self._write(ladder, season_id, user_id, rating, version)

    async def remove(self, ladder: str, season_id: UUID, user_id: UUID, version: int) -> None:
        """Убрать игрока, если версия новее записанной."""
        await self._write(ladder, season_id, user_id, None, version)

    async def replace(
        self,
        ladder: str,
        season_id: UUID,
        entries: Sequence[tuple[UUID, int, int]],
        *,
        expires: bool,
    ) -> None:
        """Атомарно заменить лидерборд: сборка во временных ключах и ``RENAME``.

        ``expires`` — метка «готов» с TTL (активный сезон) или бессрочная (завершённый).
        """
        key = board_key(ladder, season_id)
        tmp, tmp_ver = f"{key}:tmp", f"{key}:tmpver"
        async with self._guard() as redis, redis.pipeline(transaction=True) as pipe:
            pipe.delete(tmp, tmp_ver)
            for i in range(0, len(entries), _CHUNK):
                chunk = entries[i : i + _CHUNK]
                pipe.zadd(tmp, {str(user_id): rating for user_id, rating, _ in chunk})
                pipe.hset(tmp_ver, mapping={str(user_id): version for user_id, _, version in chunk})
            if entries:
                pipe.rename(tmp, key)
                pipe.rename(tmp_ver, f"{key}:ver")
            else:
                pipe.delete(key, f"{key}:ver")
            pipe.set(f"{key}:ready", 1, ex=self._ready_ttl_s if expires else None)
            await pipe.execute()

    async def exists(self, ladder: str, season_id: UUID) -> bool:
        """Есть ли метка «готов»."""
        async with self._guard() as redis:
            return bool(await redis.exists(f"{board_key(ladder, season_id)}:ready"))

    async def claim_rebuild(self, ladder: str, season_id: UUID, ttl_s: int) -> bool:
        """Занять право поставить пересборку (``SET NX EX``)."""
        async with self._guard() as redis:
            return bool(
                await redis.set(f"{board_key(ladder, season_id)}:claim", 1, nx=True, ex=ttl_s)
            )

    async def page(
        self, ladder: str, season_id: UUID, offset: int, limit: int
    ) -> list[tuple[UUID, int]]:
        """Игроки с ``offset`` по убыванию рейтинга."""
        async with self._guard() as redis:
            raw = await redis.zrevrange(
                board_key(ladder, season_id), offset, offset + limit - 1, withscores=True
            )
        # redis-py типизирует ответ широко (sync/async); при withscores это пары (член, оценка).
        rows = cast("list[tuple[bytes | str, float]]", raw)
        return [(UUID(_text(member)), int(score)) for member, score in rows]

    async def rank(self, ladder: str, season_id: UUID, user_id: UUID) -> int | None:
        """Место с 0 или ``None``."""
        async with self._guard() as redis:
            # redis-py типизирует ответ широко (sync/async); здесь это место или None.
            return cast(
                "int | None", await redis.zrevrank(board_key(ladder, season_id), str(user_id))
            )

    async def size(self, ladder: str, season_id: UUID) -> int:
        """Размер лидерборда."""
        async with self._guard() as redis:
            return int(await redis.zcard(board_key(ladder, season_id)))


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value
