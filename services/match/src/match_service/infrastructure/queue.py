"""Очереди подбора в Redis (ADR 0038): ZSET по рейтингу + HASH записи игрока.

Ключи (все в одном hash-слоте ``{mmq}``, Lua трогает их атомарно; имена ключей передаются в
``KEYS``, в ``ARGV`` — только значения):

* ``{mmq}:q:<ladder>:<format>`` — ZSET по рейтингу (score — рейтинг, member — ``user_id``);
* ``{mmq}:t:<ladder>:<format>`` — ZSET по времени входа: батч берёт **старейших** первыми,
  иначе при очереди длиннее батча верх рейтинга не обслуживался бы никогда;
* ``{mmq}:r:<ladder>:<format>`` — ZSET резервов пар (score — момент резерва): после падения
  матчера между резервом и созданием комнаты зачистка возвращает игроков в поиск;
* ``{mmq}:p:<user_id>`` — HASH записи (``ladder``, ``format``, ``rating``, ``entered_at``,
  ``state``, ``room``) с TTL: запись без heartbeat исчезает, «осиротевших» членов ZSET
  вычищает матчер;
* пара ``reserve`` проверяет токен lease очереди (fencing: матчер, проспавший lease, пару не
  создаст).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from match_service.application.ports import QueueFence, QueueRecord
from match_service.domain.queue import MATCHED, RESERVED, SEARCHING
from match_service.infrastructure.rooms import keys

_ENTER = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'ladder', ARGV[2], 'format', ARGV[3], 'rating', ARGV[4],
  'entered_at', ARGV[5], 'state', 'searching', 'room', '')
redis.call('PEXPIRE', KEYS[1], ARGV[6])
redis.call('ZADD', KEYS[2], ARGV[4], ARGV[1])
redis.call('ZADD', KEYS[3], ARGV[5], ARGV[1])
return 1
"""
_TOUCH = """
local state = redis.call('HGET', KEYS[1], 'state')
if not state then return 0 end
if state == 'searching' then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
if state == 'reserved' then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
return 1
"""
_REMOVE = """
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('ZREM', KEYS[4], ARGV[1])
return redis.call('DEL', KEYS[1])
"""
_RESERVE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
for i = 2, 3 do
  if redis.call('HGET', KEYS[i], 'state') ~= 'searching' then return 0 end
end
for i = 2, 3 do
  redis.call('HSET', KEYS[i], 'state', 'reserved', 'room', ARGV[4])
  redis.call('PEXPIRE', KEYS[i], ARGV[5])
end
redis.call('ZREM', KEYS[4], ARGV[2], ARGV[3])
redis.call('ZREM', KEYS[5], ARGV[2], ARGV[3])
redis.call('ZADD', KEYS[6], ARGV[6], ARGV[2])
redis.call('ZADD', KEYS[6], ARGV[6], ARGV[3])
return 1
"""
_CONFIRM = """
if redis.call('HGET', KEYS[1], 'state') ~= 'reserved' then return 0 end
if redis.call('HGET', KEYS[1], 'room') ~= ARGV[2] then return 0 end
redis.call('HSET', KEYS[1], 'state', 'matched')
redis.call('PEXPIRE', KEYS[1], ARGV[3])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""
_SET_ROOM = """
if redis.call('HGET', KEYS[1], 'room') == ARGV[1] then
  redis.call('HSET', KEYS[1], 'room', ARGV[2])
  return 1
end
return 0
"""
_REQUEUE = """
local state = redis.call('HGET', KEYS[1], 'state')
if state ~= 'matched' and state ~= 'reserved' then return 0 end
if redis.call('HGET', KEYS[1], 'room') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'state', 'searching', 'room', '')
redis.call('PEXPIRE', KEYS[1], ARGV[2])
redis.call('ZADD', KEYS[2], redis.call('HGET', KEYS[1], 'rating'), ARGV[3])
redis.call('ZADD', KEYS[3], redis.call('HGET', KEYS[1], 'entered_at'), ARGV[3])
redis.call('ZREM', KEYS[4], ARGV[3])
return 1
"""
_RELEASE = """
local state = redis.call('HGET', KEYS[1], 'state')
if (state == 'matched' or state == 'reserved')
    and redis.call('HGET', KEYS[1], 'room') == ARGV[1] then
  redis.call('ZREM', KEYS[2], ARGV[2])
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def parse(user_id: UUID, raw: Mapping[Any, Any]) -> QueueRecord | None:
    """HASH записи → ``QueueRecord`` (пусто — записи нет)."""
    fields = {_text(k): _text(v) for k, v in raw.items()}
    if not fields:
        return None
    return QueueRecord(
        user_id=user_id,
        ladder=fields["ladder"],
        format=fields["format"],
        rating=int(fields["rating"]),
        entered_at_ms=int(fields["entered_at"]),
        state=fields["state"],
        room_code=fields.get("room") or None,
    )


class RedisQueue:
    """``QueueStore`` на Redis.

    Args:
        redis: Клиент Redis.
        ttl_ms: Жизнь записи в поиске без heartbeat.
        reserved_ttl_ms: Жизнь записи с зарезервированной парой (короче ``matched_ttl_ms``:
            комната создаётся за миллисекунды, зависший резерв не должен жить долго).
        matched_ttl_ms: Жизнь записи после подбора (игрок идёт в комнату).
    """

    def __init__(
        self, redis: Redis, *, ttl_ms: int, matched_ttl_ms: int, reserved_ttl_ms: int | None = None
    ) -> None:
        self._redis = redis
        self._ttl_ms = ttl_ms
        self._matched_ttl_ms = matched_ttl_ms
        self._reserved_ttl_ms = reserved_ttl_ms if reserved_ttl_ms is not None else ttl_ms
        self._enter = redis.register_script(_ENTER)
        self._touch = redis.register_script(_TOUCH)
        self._remove = redis.register_script(_REMOVE)
        self._reserve = redis.register_script(_RESERVE)
        self._confirm = redis.register_script(_CONFIRM)
        self._set_room = redis.register_script(_SET_ROOM)
        self._requeue = redis.register_script(_REQUEUE)
        self._release = redis.register_script(_RELEASE)

    async def _name(self, user_id: UUID) -> str | None:
        """Имя очереди записи игрока (для ключей ZSET) или ``None``, если записи нет."""
        ladder, fmt = await self._redis.hmget(keys.queue_player(user_id), "ladder", "format")
        if ladder is None or fmt is None:
            return None
        return keys.queue_name(_text(ladder), _text(fmt))

    async def enter(
        self, user_id: UUID, ladder: str, fmt: str, rating: int, now_ms: int
    ) -> tuple[QueueRecord, bool]:
        """Встать в очередь; запись уже есть — вернуть её."""
        name = keys.queue_name(ladder, fmt)
        created = await self._enter(
            keys=[keys.queue_player(user_id), keys.queue_zset(name), keys.queue_age(name)],
            args=[str(user_id), ladder, fmt, rating, now_ms, self._ttl_ms],
        )
        record = await self.get(user_id)
        if record is None:  # истекла между вызовами: вход не потерян, повтор безопасен
            return await self.enter(user_id, ladder, fmt, rating, now_ms)
        return record, bool(created)

    async def get(self, user_id: UUID) -> QueueRecord | None:
        """Запись игрока."""
        return parse(user_id, await self._redis.hgetall(keys.queue_player(user_id)))

    async def touch(self, user_id: UUID) -> bool:
        """Heartbeat: продлевает запись в поиске и зарезервированную (комната создаётся)."""
        return bool(
            await self._touch(
                keys=[keys.queue_player(user_id)], args=[self._ttl_ms, self._reserved_ttl_ms]
            )
        )

    async def remove(self, user_id: UUID) -> None:
        """Убрать игрока из очереди (идемпотентно)."""
        name = await self._name(user_id)
        if name is None:
            await self._redis.delete(keys.queue_player(user_id))
            return
        await self._remove(
            keys=[
                keys.queue_player(user_id),
                keys.queue_zset(name),
                keys.queue_age(name),
                keys.queue_reserved(name),
            ],
            args=[str(user_id)],
        )

    async def count(self, ladder: str, fmt: str) -> int:
        """Размер очереди (ZCARD; осиротевших членов вычищает матчер)."""
        return int(await self._redis.zcard(keys.queue_zset(keys.queue_name(ladder, fmt))))

    async def waiting(self, ladder: str, fmt: str, limit: int) -> list[QueueRecord]:
        """Ищущие игроки, **старейшие первыми**; члены ZSET без живой записи вычищаются."""
        name = keys.queue_name(ladder, fmt)
        members = [
            UUID(_text(m)) for m in await self._redis.zrange(keys.queue_age(name), 0, limit - 1)
        ]
        if not members:
            return []
        async with self._redis.pipeline(transaction=False) as pipe:
            for user_id in members:
                pipe.hgetall(keys.queue_player(user_id))
            rows = await pipe.execute()
        records: list[QueueRecord] = []
        stale: list[str] = []
        for user_id, raw in zip(members, rows, strict=True):
            record = parse(user_id, raw)
            if record is None or record.state != SEARCHING:
                stale.append(str(user_id))
            else:
                records.append(record)
        if stale:
            await self._redis.zrem(keys.queue_zset(name), *stale)
            await self._redis.zrem(keys.queue_age(name), *stale)
        return records

    async def reserve(
        self, fence: QueueFence, first: UUID, second: UUID, code: str, now_ms: int
    ) -> bool:
        """Пара → ``reserved`` с кодом комнаты, только под актуальным lease очереди."""
        name = keys.queue_name(fence.ladder, fence.format)
        return bool(
            await self._reserve(
                keys=[
                    fence.key,
                    keys.queue_player(first),
                    keys.queue_player(second),
                    keys.queue_zset(name),
                    keys.queue_age(name),
                    keys.queue_reserved(name),
                ],
                args=[fence.token, str(first), str(second), code, self._reserved_ttl_ms, now_ms],
            )
        )

    async def confirm(self, ladder: str, fmt: str, first: UUID, second: UUID, code: str) -> bool:
        """Комната создана и стартует: ``reserved`` → ``matched`` у обоих."""
        done = 0
        for user_id in (first, second):
            done += int(
                await self._confirm(
                    keys=[
                        keys.queue_player(user_id),
                        keys.queue_reserved(keys.queue_name(ladder, fmt)),
                    ],
                    args=[str(user_id), code, self._matched_ttl_ms],
                )
            )
        return done == 2  # noqa: PLR2004 — оба игрока

    async def stale_reserved(self, ladder: str, fmt: str, before_ms: int) -> list[QueueRecord]:
        """Резервы старше ``before_ms`` (матчер упал до создания комнаты); мусор убирается."""
        rsv = keys.queue_reserved(keys.queue_name(ladder, fmt))
        members = [UUID(_text(m)) for m in await self._redis.zrangebyscore(rsv, "-inf", before_ms)]
        found: list[QueueRecord] = []
        for user_id in members:
            record = await self.get(user_id)
            if record is not None and record.state == RESERVED:
                found.append(record)
            else:
                await self._redis.zrem(rsv, str(user_id))
        return found

    async def set_room(self, users: Sequence[UUID], old: str, new: str) -> None:
        """Сменить код комнаты (коллизия)."""
        for user_id in users:
            await self._set_room(keys=[keys.queue_player(user_id)], args=[old, new])

    async def requeue(self, user_id: UUID, code: str) -> bool:
        """Вернуть в поиск с прежним ``entered_at``."""
        name = await self._name(user_id)
        if name is None:
            return False
        return bool(
            await self._requeue(
                keys=[
                    keys.queue_player(user_id),
                    keys.queue_zset(name),
                    keys.queue_age(name),
                    keys.queue_reserved(name),
                ],
                args=[code, self._ttl_ms, str(user_id)],
            )
        )

    async def release(self, user_id: UUID, code: str) -> None:
        """Матч начался: запись не нужна."""
        name = await self._name(user_id)
        if name is None:
            return
        await self._release(
            keys=[keys.queue_player(user_id), keys.queue_reserved(name)],
            args=[code, str(user_id)],
        )


__all__ = ["MATCHED", "RESERVED", "SEARCHING", "RedisQueue", "parse"]
