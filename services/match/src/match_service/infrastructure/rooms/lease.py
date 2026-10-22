"""Lease владельца комнаты с fencing-эпохой (D7, ADR 0034).

``{room:CODE}:owner = "<instance>|<epoch>"`` с TTL; захват увеличивает ``{room:CODE}:epoch``.
Продление, освобождение и запись снимка — Lua «owner == мой токен»: владелец, проспавший
TTL (пауза GC, сеть), получает отказ и бросает комнату, а его поздняя запись не затрёт
снимок нового владельца.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from match_service.infrastructure.rooms import keys

_ACQUIRE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
local epoch = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], ARGV[1] .. '|' .. epoch, 'PX', ARGV[2])
return epoch
"""
_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
_CAS = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'PX', ARGV[3])
return 1
"""


@dataclass(frozen=True, slots=True)
class Grant:
    """Владение комнатой: код и эпоха."""

    code: str
    epoch: int
    token: str


class Lease:
    """Операции lease одного инстанса (``instance`` уникален на процесс)."""

    def __init__(self, redis: Redis, instance: str, ttl_ms: int) -> None:
        if "|" in instance:
            raise ValueError("instance не должен содержать '|'")
        self._redis = redis
        self._instance = instance
        self._ttl_ms = ttl_ms
        self._acquire = redis.register_script(_ACQUIRE)
        self._renew = redis.register_script(_RENEW)
        self._release = redis.register_script(_RELEASE)
        self._cas = redis.register_script(_CAS)

    @property
    def instance(self) -> str:
        """Идентификатор инстанса."""
        return self._instance

    async def acquire(self, code: str) -> Grant | None:
        """Захватить свободную комнату; ``None`` — у неё есть владелец."""
        epoch = int(
            await self._acquire(
                keys=[keys.owner(code), keys.epoch(code)], args=[self._instance, self._ttl_ms]
            )
        )
        if epoch == 0:
            return None
        return Grant(code=code, epoch=epoch, token=f"{self._instance}|{epoch}")

    async def renew(self, grant: Grant) -> bool:
        """Продлить; ``False`` — lease потерян (комнату взял другой)."""
        return bool(
            await self._renew(keys=[keys.owner(grant.code)], args=[grant.token, self._ttl_ms])
        )

    async def release(self, grant: Grant) -> bool:
        """Отпустить, если lease всё ещё наш."""
        return bool(await self._release(keys=[keys.owner(grant.code)], args=[grant.token]))

    async def write(self, grant: Grant, snapshot: str, ttl_ms: int) -> bool:
        """Записать снимок, только если lease наш (CAS по токену с эпохой)."""
        result = await self._cas(
            keys=[keys.owner(grant.code), keys.snapshot(grant.code)],
            args=[grant.token, snapshot, ttl_ms],
        )
        return bool(result)

    async def holder(self, code: str) -> tuple[str, int] | None:
        """Текущий владелец ``(instance, epoch)`` или ``None``."""
        raw = await self._redis.get(keys.owner(code))
        if raw is None:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        instance, _, epoch = value.rpartition("|")
        return instance, int(epoch)
