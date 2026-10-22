"""Снимки комнат в Redis: чтение без владельца, создание, индексы (D7, ADR 0034).

Запись снимка владельцем — только Lua-CAS ``Lease.write`` (fencing-эпоха); здесь —
создание новой комнаты (``SET NX``: код уникален) и чтение для api (``state``, ``tick``,
списки). Индексы ``rooms:active``/``rooms:open`` обновляет владелец после снимка.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from match_service.domain.errors import MatchUnavailableError
from match_service.domain.model import Phase, Presence
from match_service.domain.room import Room
from match_service.infrastructure.rooms import keys
from match_service.infrastructure.rooms.codec import SnapshotError, dumps, loads

log = structlog.get_logger(__name__)


def is_open(room: Room) -> bool:
    """Комнату показывают в списке открытых лобби."""
    seated = sum(1 for s in room.seats if s.presence is not Presence.LEFT)
    return (
        room.phase is Phase.LOBBY
        and room.settings.visibility == "public"
        and seated < room.settings.players_max
    )


class RedisRoomStore:
    """``RoomStore`` на Redis."""

    def __init__(self, redis: Redis, *, snapshot_ttl_ms: int) -> None:
        self._redis = redis
        self._ttl_ms = snapshot_ttl_ms

    async def load(self, code: str) -> Room | None:
        """Снимок комнаты; повреждённый снимок — как отсутствующий (с логом)."""
        try:
            raw = await self._redis.get(keys.snapshot(code))
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc
        if raw is None:
            return None
        try:
            return loads(raw)
        except SnapshotError:
            log.exception("match.snapshot_unreadable", room=code)
            return None

    async def create(self, room: Room) -> bool:
        """Новая комната: снимок ``SET NX``, индексы, сигнал владельцам."""
        try:
            created = await self._redis.set(
                keys.snapshot(room.code), dumps(room), nx=True, px=self._ttl_ms
            )
            if not created:
                return False
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.zadd(keys.ACTIVE, {room.code: room.created_at_ms})
                if is_open(room):
                    pipe.zadd(keys.OPEN, {room.code: room.created_at_ms})
                pipe.rpush(keys.ORPHANS, room.code)
                await pipe.execute()
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc
        return True

    async def _many(self, codes: list[str]) -> list[Room]:
        if not codes:
            return []
        raws = await self._redis.mget([keys.snapshot(c) for c in codes])
        rooms: list[Room] = []
        for code, raw in zip(codes, raws, strict=True):
            if raw is None:
                continue
            try:
                rooms.append(loads(raw))
            except SnapshotError:
                log.warning("match.snapshot_unreadable", room=code)
        return rooms

    async def open_rooms(
        self, cursor: tuple[int, str] | None, limit: int
    ) -> tuple[list[Room], tuple[int, str] | None]:
        """Открытые лобби, новые первыми (keyset ``(created_at_ms, code)``)."""
        try:
            top = "+inf" if cursor is None else cursor[0]
            # Запас на комнаты, закрытые после последнего обновления индекса.
            # Any: redis-py отдаёт [(член, score)] при withscores.
            raw: Any = await self._redis.zrevrangebyscore(
                keys.OPEN, top, "-inf", start=0, num=limit * 3 + 10, withscores=True
            )
            pairs = [(int(score), _text(code)) for code, score in raw]
            if cursor is not None:
                pairs = [p for p in pairs if (p[0], p[1]) < cursor]
            pairs.sort(reverse=True)
            rooms = {r.code: r for r in await self._many([c for _, c in pairs])}
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc
        out: list[Room] = []
        last: tuple[int, str] | None = None
        for score, code in pairs:
            room = rooms.get(code)
            if room is None or not is_open(room):
                continue
            out.append(room)
            last = (score, code)
            if len(out) >= limit:
                break
        return out, (last if len(out) >= limit else None)

    async def active_rooms(
        self, cursor: str | None, limit: int
    ) -> tuple[list[tuple[Room, int | None]], str | None]:
        """Активные комнаты (новые первыми) с эпохой владельца; курсор — смещение."""
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        try:
            codes = [
                _text(c)
                for c in await self._redis.zrevrange(keys.ACTIVE, offset, offset + limit - 1)
            ]
            rooms = await self._many(codes)
            owners = await self._redis.mget([keys.owner(r.code) for r in rooms]) if rooms else []
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc
        out: list[tuple[Room, int | None]] = []
        for room, owner in zip(rooms, owners, strict=True):
            epoch = int(_text(owner).rpartition("|")[2]) if owner is not None else None
            out.append((room, epoch))
        nxt = str(offset + limit) if len(codes) == limit else None
        return out, nxt

    async def index(self, room: Room) -> None:
        """Обновить индексы после снимка (владелец)."""
        async with self._redis.pipeline(transaction=False) as pipe:
            if room.is_closed:
                pipe.zrem(keys.ACTIVE, room.code)
                pipe.zrem(keys.OPEN, room.code)
            elif is_open(room):
                pipe.zadd(keys.OPEN, {room.code: room.created_at_ms})
            else:
                pipe.zrem(keys.OPEN, room.code)
            await pipe.execute()

    async def signal_orphan(self, code: str) -> None:
        """Комната без владельца: сигнал другим инстансам (handoff)."""
        await self._redis.rpush(keys.ORPHANS, code)

    async def expire_closed(self, room: Room, ttl_s: int) -> None:
        """Ключи закрытой комнаты истекают (снимок ещё доступен админке и ресинку)."""
        async with self._redis.pipeline(transaction=False) as pipe:
            for key in keys.room_keys(room.code):
                pipe.expire(key, ttl_s)
            await pipe.execute()


class RedisIdempotentCreates:
    """``IdempotentCreates``: ключ ``Idempotency-Key`` создания комнаты в Redis."""

    def __init__(self, redis: Redis, *, ttl_s: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def get(self, user_id: UUID, key: str) -> tuple[str, str] | None:
        """``(отпечаток, код)`` или ``None``."""
        raw = await self._redis.get(keys.create_idempotency(user_id, key))
        if raw is None:
            return None
        fingerprint, _, code = _text(raw).partition("|")
        return fingerprint, code

    async def put(self, user_id: UUID, key: str, fingerprint: str, code: str) -> None:
        """Запомнить результат."""
        await self._redis.set(
            keys.create_idempotency(user_id, key), f"{fingerprint}|{code}", ex=self._ttl_s
        )


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
