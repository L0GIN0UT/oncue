"""Команды комнаты через Redis Stream и ответы через список (D6, ADR 0034).

* api: ``XADD {room:CODE}:cmd`` → ``BLPOP {room:CODE}:reply:{user}:{op}`` не дольше
  ``GV_MATCH__REPLY_TIMEOUT_MS`` (иначе 202);
* владелец: ``XREAD`` после курсора из снимка → применить → снимок (с новым курсором) →
  ответы ``LPUSH`` → ``XDEL`` применённых. Курсор в снимке — подтверждение команд: при
  захвате новый владелец продолжает с последнего записанного снимка, команды, применённые
  без снимка, применяются заново (их ответы ещё не уходили).

Тут же — heartbeat игроков (``{room:CODE}:seen``) и ``player:{id}:room`` (D10).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from match_service.application.ports import Json, RoomCommand, RoomPointer
from match_service.domain.errors import MatchUnavailableError
from match_service.infrastructure.rooms import keys

if TYPE_CHECKING:
    from match_service.infrastructure.rooms.reader import SharedCommandReader

FIELD = "c"
_RELEASE_PLAYER = """
local v = redis.call('GET', KEYS[1])
if v and (v == ARGV[1] or string.sub(v, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '|') then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_REPLACE_PLAYER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
  return 1
end
return 0
"""
_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def encode_command(command: RoomCommand) -> str:
    """Команда для поля потока."""
    return json.dumps(
        {
            "op_id": command.op_id,
            "kind": command.kind,
            "user_id": str(command.user_id),
            "data": command.data,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def decode_command(raw: str) -> RoomCommand:
    """Команда из поля потока."""
    data = json.loads(raw)
    return RoomCommand(
        op_id=str(data["op_id"]),
        kind=str(data["kind"]),
        user_id=UUID(str(data["user_id"])),
        data=dict(data.get("data") or {}),
    )


class RedisCommandBus:
    """``CommandBus`` на Redis (процесс api)."""

    def __init__(self, redis: Redis, *, maxlen: int) -> None:
        self._redis = redis
        self._maxlen = maxlen

    async def send(self, code: str, command: RoomCommand) -> None:
        """Положить команду в поток комнаты."""
        try:
            await self._redis.xadd(
                keys.commands(code),
                {FIELD: encode_command(command)},
                maxlen=self._maxlen,
                approximate=True,
            )
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc

    async def wait_reply(self, code: str, command: RoomCommand, timeout_ms: int) -> Json | None:
        """Ответ владельца или ``None`` по таймауту."""
        key = keys.reply(code, command.user_id, command.op_id)
        try:
            item = await self._redis.blpop([key], timeout=timeout_ms / 1000)
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc
        if item is None:
            return None
        reply: Json = json.loads(_text(item[1]))
        return reply


def parse_field(raw: Any) -> RoomCommand | None:  # Any: поле записи Redis
    """Команда из поля записи потока; битая или пустая запись - ``None``."""
    if raw is None:
        return None
    try:
        return decode_command(_text(raw))
    except (ValueError, KeyError, TypeError):
        return None


class CommandStream:
    """Поток команд глазами владельца.

    Чтение - через общий ``SharedCommandReader`` инстанса (ADR 0063): одно блокирующее
    ``XREAD`` на все комнаты, а не по одному на комнату.
    """

    def __init__(self, redis: Redis, *, reply_ttl_s: int, reader: SharedCommandReader) -> None:
        self._redis = redis
        self._reply_ttl_s = reply_ttl_s
        self._reader = reader

    async def read(
        self, code: str, cursor: str, *, block_ms: int, count: int
    ) -> list[tuple[str, RoomCommand | None]]:
        """Команды после ``cursor`` (ждать не дольше ``block_ms``); битая запись - ``None``."""
        return await self._reader.read(code, cursor, block_ms=block_ms, count=count)

    def detach(self, code: str) -> None:
        """Комната ушла от инстанса: общий читатель её больше не читает."""
        self._reader.detach(code)

    async def reply(self, code: str, command: RoomCommand, reply: Json) -> None:
        """Отдать ответ отправителю команды."""
        key = keys.reply(code, command.user_id, command.op_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.lpush(key, json.dumps(reply, ensure_ascii=False, default=str))
            pipe.expire(key, self._reply_ttl_s)
            await pipe.execute()

    async def ack(self, code: str, entry_ids: Sequence[str]) -> None:
        """Удалить применённые команды (снимок с курсором уже записан)."""
        if entry_ids:
            await self._redis.xdel(keys.commands(code), *entry_ids)


class RedisPresence:
    """``PresenceStore``: heartbeat в HASH комнаты (без владельца)."""

    def __init__(self, redis: Redis, *, ttl_s: int) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def touch(self, code: str, user_id: UUID, now_ms: int) -> None:
        """Отметить игрока."""
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.hset(keys.seen(code), str(user_id), now_ms)
                pipe.expire(keys.seen(code), self._ttl_s)
                await pipe.execute()
        except (RedisError, OSError) as exc:
            raise MatchUnavailableError("комнаты временно недоступны") from exc

    async def seen(self, code: str) -> dict[UUID, int]:
        """Последний heartbeat игроков."""
        raw = await self._redis.hgetall(keys.seen(code))
        out: dict[UUID, int] = {}
        for k, v in raw.items():
            try:
                out[UUID(_text(k))] = int(_text(v))
            except ValueError:
                continue
        return out


class RedisPlayerRooms:
    """``PlayerRooms``: ``player:{id}:room = code|ms`` с TTL (одна активная комната, D10).

    Замена — Lua compare-and-set по старому значению, освобождение — по коду комнаты;
    замок создания ``player:{id}:creating`` — случайный токен и compare-and-delete.
    """

    def __init__(self, redis: Redis, *, ttl_ms: int) -> None:
        self._redis = redis
        self._ttl_ms = ttl_ms
        self._release = redis.register_script(_RELEASE_PLAYER)
        self._replace = redis.register_script(_REPLACE_PLAYER)
        self._unlock = redis.register_script(_UNLOCK)

    async def get(self, user_id: UUID) -> str | None:
        """Код активной комнаты."""
        found = await self.pointer(user_id)
        return found.code if found is not None else None

    async def pointer(self, user_id: UUID) -> RoomPointer | None:
        """Указатель целиком."""
        raw = await self._redis.get(keys.player(user_id))
        return RoomPointer.parse(_text(raw)) if raw is not None else None

    async def claim(self, user_id: UUID, code: str, now_ms: int) -> bool:
        """Занять, если свободно (или уже эта комната)."""
        value = f"{code}|{now_ms}"
        if await self._redis.set(keys.player(user_id), value, nx=True, px=self._ttl_ms):
            return True
        return await self.get(user_id) == code

    async def replace(self, user_id: UUID, old: RoomPointer, code: str, now_ms: int) -> bool:
        """Заменить, если указатель всё ещё ``old``."""
        done = await self._replace(
            keys=[keys.player(user_id)], args=[old.raw, f"{code}|{now_ms}", self._ttl_ms]
        )
        return bool(done)

    async def assign(self, user_id: UUID, code: str, now_ms: int) -> None:
        """Записать безусловно."""
        await self._redis.set(keys.player(user_id), f"{code}|{now_ms}", px=self._ttl_ms)

    async def release(self, user_id: UUID, code: str) -> None:
        """Освободить, если записана эта комната."""
        await self._release(keys=[keys.player(user_id)], args=[code])

    async def lock_create(self, user_id: UUID, token: str, ttl_ms: int) -> bool:
        """Замок создания комнаты (``SET NX PX``); ``False`` — создание уже идёт."""
        return bool(await self._redis.set(keys.creating(user_id), token, nx=True, px=ttl_ms))

    async def create_holder(self, user_id: UUID) -> str | None:
        """Токен текущего замка создания."""
        raw = await self._redis.get(keys.creating(user_id))
        return _text(raw) if raw is not None else None

    async def unlock_create(self, user_id: UUID, token: str) -> None:
        """Снять замок, если он всё ещё наш."""
        await self._unlock(keys=[keys.creating(user_id)], args=[token])
