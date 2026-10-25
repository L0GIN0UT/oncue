"""Общий читатель потоков команд на реальном Redis (ADR 0063)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest
from match_service.application.ports import RoomCommand
from match_service.infrastructure.rooms.reader import READER_ERRORS, SharedCommandReader
from match_service.infrastructure.rooms.stream import FIELD, RedisCommandBus, parse_field
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration

POLL_MS = 200


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    client = Redis.from_url(infra.redis_url(), socket_timeout=2)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
async def reader(redis: Redis) -> AsyncIterator[SharedCommandReader]:
    shared = SharedCommandReader(
        redis, poll_ms=POLL_MS, batch=50, parse=parse_field, field_name=FIELD
    )
    task = asyncio.create_task(shared.run())
    try:
        yield shared
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _command(op: str) -> RoomCommand:
    return RoomCommand(op_id=op, kind="ready", user_id=uuid7(), data={})


async def _blocked_xread(redis: Redis) -> int:
    clients = await redis.client_list()
    return sum(1 for c in clients if c.get("cmd") == "xread" and "b" in c.get("flags", ""))


async def test_many_rooms_share_one_blocking_connection(
    redis: Redis, reader: SharedCommandReader
) -> None:
    codes = [f"ROOM{i:04d}" for i in range(30)]
    waits = [
        asyncio.create_task(reader.read(code, "0-0", block_ms=1500, count=10)) for code in codes
    ]
    await asyncio.sleep(0.3)
    assert reader.watched == len(codes)
    assert await _blocked_xread(redis) == 1  # не зависит от числа комнат
    for wait in waits:
        wait.cancel()
    await asyncio.gather(*waits, return_exceptions=True)


async def test_commands_arrive_in_order_without_loss_when_rooms_come_and_go(
    redis: Redis, reader: SharedCommandReader
) -> None:
    bus = RedisCommandBus(redis, maxlen=1000)
    got: list[str] = []

    async def consume(code: str, cursor: str, want: int) -> None:
        while len(got) < want:
            for entry_id, command in await reader.read(code, cursor, block_ms=500, count=3):
                assert command is not None
                got.append(command.op_id)
                cursor = entry_id

    consumer = asyncio.create_task(consume("ALPHA000", "0-0", 40))
    await asyncio.sleep(0.1)
    for i in range(40):
        await bus.send("ALPHA000", _command(f"a{i}"))
        if i % 5 == 0:  # состав набора меняется посреди потока: пересборка вызова
            reader.detach(f"TEMP{i:04d}")
            await reader.read(f"TEMP{i:04d}", "0-0", block_ms=1, count=1)
    await asyncio.wait_for(consumer, 10)
    assert got == [f"a{i}" for i in range(40)]


async def test_cursor_rollback_rereads_from_snapshot(
    redis: Redis, reader: SharedCommandReader
) -> None:
    bus = RedisCommandBus(redis, maxlen=1000)
    for i in range(3):
        await bus.send("BETA0000", _command(f"b{i}"))
    first = await reader.read("BETA0000", "0-0", block_ms=1000, count=10)
    assert [c.op_id for _, c in first if c] == ["b0", "b1", "b2"]
    # Откат после сбоя итерации: актёр вернулся к курсору снимка.
    again = await reader.read("BETA0000", first[0][0], block_ms=1000, count=10)
    assert [c.op_id for _, c in again if c] == ["b1", "b2"]


async def test_detached_room_is_not_read(redis: Redis, reader: SharedCommandReader) -> None:
    await reader.read("GAMMA000", "0-0", block_ms=1, count=1)
    assert reader.watched == 1
    reader.detach("GAMMA000")
    await asyncio.sleep(0.1)
    assert reader.watched == 0
    assert await _blocked_xread(redis) == 0


async def test_unreadable_entry_is_delivered_as_none(
    redis: Redis, reader: SharedCommandReader
) -> None:
    await redis.xadd("{room:DELTA000}:cmd", {FIELD: "не json"})
    entries = await reader.read("DELTA000", "0-0", block_ms=1000, count=10)
    assert len(entries) == 1
    assert entries[0][1] is None


async def test_redis_error_does_not_kill_reader(redis: Redis) -> None:
    class Flaky(Redis):  # type: ignore[misc]  # тест: подмена xread на сбойный
        calls = 0

        async def xread(self, *args: object, **kwargs: object) -> object:
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise ConnectionError("down")
            return await super().xread(*args, **kwargs)  # type: ignore[arg-type]

    flaky = Flaky(connection_pool=redis.connection_pool)
    shared = SharedCommandReader(
        flaky, poll_ms=POLL_MS, batch=10, parse=parse_field, field_name=FIELD
    )
    task = asyncio.create_task(shared.run())
    try:
        bus = RedisCommandBus(redis, maxlen=100)
        waiter = asyncio.create_task(shared.read("EPSILON0", "0-0", block_ms=3000, count=5))
        await asyncio.sleep(0.05)
        await bus.send("EPSILON0", _command("e1"))
        entries = await asyncio.wait_for(waiter, 5)
        assert [c.op_id for _, c in entries if c] == ["e1"]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_unexpected_error_in_deliver_does_not_stop_reading_forever(redis: Redis) -> None:
    class Crashy(SharedCommandReader):
        crashed = 0

        def _deliver(self, plan: dict[str, str], result: object) -> None:
            if Crashy.crashed == 0:
                Crashy.crashed = 1
                raise RuntimeError("баг в разборе")
            super()._deliver(plan, result)

    shared = Crashy(
        redis,
        poll_ms=20,
        batch=10,
        parse=parse_field,
        field_name=FIELD,
        backoff_max_s=0.1,
    )
    before = READER_ERRORS._value.get()
    task = asyncio.create_task(shared.run())
    try:
        bus = RedisCommandBus(redis, maxlen=100)
        waiter = asyncio.create_task(shared.read("ZETA0000", "0-0", block_ms=5000, count=5))
        await asyncio.sleep(0.05)
        await bus.send("ZETA0000", _command("z1"))
        entries = await asyncio.wait_for(waiter, 5)
        assert [c.op_id for _, c in entries if c] == ["z1"]  # прочитано после сбоя, без потерь
        assert not task.done()
        assert READER_ERRORS._value.get() == before + 1
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
