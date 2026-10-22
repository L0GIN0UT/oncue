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
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from match_service.application.ports import Json, RoomCommand
from match_service.domain.errors import MatchUnavailableError
from match_service.infrastructure.rooms import keys

FIELD = "c"
_RELEASE_PLAYER = """
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


class CommandStream:
    """Поток команд глазами владельца."""

    def __init__(self, redis: Redis, *, reply_ttl_s: int) -> None:
        self._redis = redis
        self._reply_ttl_s = reply_ttl_s

    async def read(
        self, code: str, cursor: str, *, block_ms: int, count: int
    ) -> list[tuple[str, RoomCommand | None]]:
        """Команды после ``cursor`` (ждать не дольше ``block_ms``); битая запись — ``None``."""
        # Any: ответ redis-py XREAD — [[ключ, [(id, {поле: значение})]]].
        result: Any = await self._redis.xread(
            {keys.commands(code): cursor}, count=count, block=max(1, block_ms)
        )
        out: list[tuple[str, RoomCommand | None]] = []
        for _, entries in result or []:
            for entry_id, fields in entries:
                raw = fields.get(FIELD.encode()) or fields.get(FIELD)
                try:
                    command = decode_command(_text(raw)) if raw is not None else None
                except (ValueError, KeyError, TypeError):
                    command = None
                out.append((_text(entry_id), command))
        return out

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
    """``PlayerRooms``: ``player:{id}:room`` (одна активная комната на игрока, D10)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._release = redis.register_script(_RELEASE_PLAYER)

    async def get(self, user_id: UUID) -> str | None:
        """Код активной комнаты."""
        raw = await self._redis.get(keys.player(user_id))
        return _text(raw) if raw is not None else None

    async def claim(self, user_id: UUID, code: str) -> bool:
        """Занять, если свободно (или уже эта комната)."""
        if await self._redis.set(keys.player(user_id), code, nx=True):
            return True
        return await self.get(user_id) == code

    async def assign(self, user_id: UUID, code: str) -> None:
        """Записать безусловно."""
        await self._redis.set(keys.player(user_id), code)

    async def release(self, user_id: UUID, code: str) -> None:
        """Освободить, если записана эта комната."""
        await self._release(keys=[keys.player(user_id)], args=[code])

    async def lock_create(self, user_id: UUID, ttl_ms: int) -> bool:
        """Замок создания комнаты (``SET NX PX``); ``False`` — создание уже идёт."""
        return bool(await self._redis.set(keys.creating(user_id), "1", nx=True, px=ttl_ms))

    async def unlock_create(self, user_id: UUID) -> None:
        """Снять замок создания."""
        await self._redis.delete(keys.creating(user_id))
