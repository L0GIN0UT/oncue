"""Процесс ``rooms`` на реальном Redis (R4, D6, D7, ADR 0034).

Два runner-а; падение одного — захват комнаты не дольше TTL lease (+ период обхода);
«зомби» с устаревшей эпохой не перезаписывает снимок; SIGTERM — handoff без потери
команд; ответы команд через поток и список.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from match_doubles import (
    FakeAssets,
    FakePlans,
    MemoryPublisher,
    MemoryRecords,
    duel_body,
    factory,
    lobby_body,
)
from match_service.application.owner import OwnerSettings, RoomOwner
from match_service.application.ports import RoomCommand
from match_service.domain.model import Phase
from match_service.domain.room import Room, default_settings
from match_service.infrastructure.rooms import keys
from match_service.infrastructure.rooms.codec import dumps
from match_service.infrastructure.rooms.lease import Lease
from match_service.infrastructure.rooms.runner import RoomsRunner, RunnerSettings
from match_service.infrastructure.rooms.snapshot import RedisRoomStore
from match_service.infrastructure.rooms.stream import RedisCommandBus, RedisPlayerRooms
from redis.asyncio import Redis

from gv_core.clock import SystemClock
from gv_core.ids import uuid7
from gv_core.random import SystemRandom
from gv_service.runners import supervise_runners
from gv_service.settings import RunnersSettings
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration

TTL_MS = 1000
SCAN_MS = 200


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    client = Redis.from_url(infra.redis_url(), socket_timeout=2)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@dataclass
class Node:
    """Инстанс процесса ``rooms``."""

    runner: RoomsRunner
    stop: asyncio.Event
    task: asyncio.Task[None]


@dataclass
class Cluster:
    redis: Redis
    publisher: MemoryPublisher = field(default_factory=MemoryPublisher)
    nodes: list[Node] = field(default_factory=list)
    ops: int = 0

    def __post_init__(self) -> None:
        self.store = RedisRoomStore(self.redis, snapshot_ttl_ms=3_600_000)
        self.bus = RedisCommandBus(self.redis, maxlen=1000)
        self.factory = factory()
        self.bodies = {"music.duel.classic": duel_body(), "film.lobby.collection": lobby_body()}

    def node(self, name: str, *, supervised: bool = True) -> Node:
        """Инстанс; без супервизора отмена задачи — «kill -9» (без handoff)."""
        owner = RoomOwner(
            factory=self.factory,
            plans=FakePlans(self.bodies),
            assets=FakeAssets(),
            records=MemoryRecords(),
            store=self.store,
            player_rooms=RedisPlayerRooms(self.redis),
            clock=SystemClock(),
            random=SystemRandom(),
            new_id=uuid7,
            settings=OwnerSettings(
                op_window=64, memory_limit=100, idle_ms=600_000, presence_timeout_ms=60_000
            ),
        )
        runner = RoomsRunner(
            self.redis,
            owner=owner,
            publisher=self.publisher,
            store=self.store,
            clock=SystemClock(),
            settings=RunnerSettings(
                instance=name,
                lease_ttl_ms=TTL_MS,
                renew_ms=300,
                poll_ms=50,
                max_rooms=10,
                scan_ms=SCAN_MS,
                snapshot_ttl_ms=3_600_000,
                closed_ttl_s=60,
                reply_ttl_s=30,
                presence_ttl_s=60,
            ),
        )
        stop = asyncio.Event()
        settings = RunnersSettings(stop_timeout_s=2, handoff_timeout_s=2)
        task = asyncio.create_task(
            supervise_runners([runner], stop, settings) if supervised else runner.run(stop)
        )
        node = Node(runner, stop, task)
        self.nodes.append(node)
        return node

    async def room(self, key: str = "film.lobby.collection") -> tuple[str, UUID]:
        tpl = self.factory.template(key, 1, self.bodies[key])
        settings = default_settings(tpl.template)
        if tpl.template.uses_collection:
            from dataclasses import replace

            settings = replace(settings, collection_ids=(uuid7(),))
        host = uuid7()
        code = "".join("ABCDEFGHJKMNPQRS"[b % 16] for b in uuid7().bytes[-8:])
        room = Room.create(
            code=code,
            mode=tpl.mode,
            template=tpl.template,
            settings=settings,
            host_id=host,
            nickname="host",
            now_ms=int(SystemClock().now().timestamp() * 1000),
        )
        assert await self.store.create(room)
        return code, host

    async def command(
        self, code: str, user: UUID, kind: str, *, op_id: str | None = None, **data: Any
    ) -> dict[str, Any] | None:
        self.ops += 1
        cmd = RoomCommand(op_id=op_id or f"op-{self.ops:08d}", kind=kind, user_id=user, data=data)
        await self.bus.send(code, cmd)
        return await self.bus.wait_reply(code, cmd, 1500)

    async def owner_of(self, code: str) -> tuple[str, int] | None:
        return await Lease(self.redis, "probe", TTL_MS).holder(code)


async def _until(predicate: Callable[[], Any], timeout_s: float = 5.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not await predicate():  # noqa: ASYNC110 — опрос внешнего состояния Redis
            await asyncio.sleep(0.02)


@pytest.fixture
async def cluster(redis: Redis) -> AsyncIterator[Cluster]:
    c = Cluster(redis)
    try:
        yield c
    finally:
        for node in c.nodes:
            node.stop.set()
        for node in c.nodes:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(node.task, 5)


async def test_command_gets_reply_and_snapshot_has_cursor(cluster: Cluster) -> None:
    cluster.node("a")
    code, host = await cluster.room()
    joiner = uuid7()
    reply = await cluster.command(code, joiner, "join", nickname="b")
    assert reply is not None and reply["status"] == 200, reply
    room = await cluster.store.load(code)
    assert room is not None and len(room.seats) == 2 and room.cursor != "0-0"
    # Применённая команда удалена из потока (подтверждение — курсор снимка).
    assert await cluster.redis.xlen(keys.commands(code)) == 0
    lobby = [m for m in cluster.publisher.of(f"room:{code}") if m["kind"] == "lobby"]
    assert lobby and lobby[-1]["version"] == room.version
    del host


async def test_crashed_owner_is_replaced_within_lease_ttl(cluster: Cluster) -> None:
    a = cluster.node("a", supervised=False)
    code, host = await cluster.room()
    await _until(lambda: _owned_by(cluster, code, "a"))
    b = cluster.node("b")
    a.task.cancel()  # «kill -9»: без handoff, lease не отпущен
    with contextlib.suppress(asyncio.CancelledError):
        await a.task
    loop = asyncio.get_running_loop()
    killed = loop.time()
    await _until(lambda: _owned_by(cluster, code, "b"), timeout_s=TTL_MS / 1000 + 2)
    assert loop.time() - killed <= (TTL_MS + SCAN_MS) / 1000 + 0.5
    holder = await cluster.owner_of(code)
    assert holder is not None and holder[1] == 2
    reply = await cluster.command(code, host, "ready", ready=True)
    assert reply is not None and reply["status"] == 200
    del b


async def test_zombie_with_stale_epoch_cannot_write(cluster: Cluster) -> None:
    code, host = await cluster.room()
    zombie = Lease(cluster.redis, "zombie", TTL_MS)
    grant = await zombie.acquire(code)
    assert grant is not None
    room = await cluster.store.load(code)
    assert room is not None
    # Lease истёк (пауза процесса), комнату взял другой инстанс.
    await cluster.redis.delete(keys.owner(code))
    fresh = Lease(cluster.redis, "fresh", TTL_MS)
    new_grant = await fresh.acquire(code)
    assert new_grant is not None and new_grant.epoch == grant.epoch + 1
    room.version += 100
    assert await zombie.write(grant, dumps(room), 60_000) is False
    assert await zombie.renew(grant) is False
    current = await cluster.store.load(code)
    assert current is not None and current.version == room.version - 100
    assert await fresh.write(new_grant, dumps(room), 60_000) is True
    del host


async def test_sigterm_hands_off_without_losing_commands(cluster: Cluster) -> None:
    a = cluster.node("a")
    code, host = await cluster.room()
    await _until(lambda: _owned_by(cluster, code, "a"))
    cluster.node("b")
    users = [uuid7() for _ in range(4)]
    sends = [
        asyncio.create_task(
            cluster.command(code, u, "join", op_id=f"op-join-{i:04d}", nickname=f"p{i}")
        )
        for i, u in enumerate(users)
    ]
    await asyncio.sleep(0.01)
    a.stop.set()  # SIGTERM: перестать брать работу → handoff
    await asyncio.wait_for(a.task, 5)
    await asyncio.gather(*sends)
    await _until(lambda: _owned_by(cluster, code, "b"), timeout_s=2)

    async def all_joined() -> bool:
        room = await cluster.store.load(code)
        return room is not None and {s.user_id for s in room.seats} >= set(users)

    await _until(all_joined, timeout_s=3)
    # Повтор с тем же op_id после 202 — сохранённый ответ, без повторного применения.
    for i, user in enumerate(users):
        again = await cluster.command(code, user, "join", op_id=f"op-join-{i:04d}", nickname="x")
        assert again is not None and again["status"] == 200
    room = await cluster.store.load(code)
    assert room is not None and len(room.seats) == 5
    del host


async def test_closed_room_leaves_indexes_and_owner(cluster: Cluster) -> None:
    cluster.node("a")
    code, host = await cluster.room()
    reply = await cluster.command(code, host, "leave")
    assert reply is not None and reply["status"] == 200

    async def gone() -> bool:
        return (
            await cluster.redis.zscore(keys.ACTIVE, code) is None
            and await cluster.owner_of(code) is None
        )

    await _until(gone)
    room = await cluster.store.load(code)
    assert room is not None and room.phase is Phase.CLOSED
    assert await cluster.redis.ttl(keys.snapshot(code)) > 0


async def _owned_by(cluster: Cluster, code: str, instance: str) -> bool:
    holder = await cluster.owner_of(code)
    return holder is not None and holder[0] == instance
