"""Матч до итога на настоящем runner-е по таймерам (e2e CI: матч вставал посреди раундов).

Реальный Redis и системные часы, шаблоны — сиды с короткими окнами, game и media — фейки
testkit. Игроки пропускают, промахиваются и молчат: уровни закрываются только по
дедлайну, ревил и следующий раунд — тоже без команд.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from match_doubles import MemoryRecords, MemorySubscriptions, factory, plan_factory, seed_bodies
from match_service.application.owner import OwnerSettings, RoomOwner
from match_service.application.ports import RoomCommand
from match_service.domain.model import Phase
from match_service.domain.room import Room, default_settings
from match_service.infrastructure.rooms.runner import RoomsRunner, RunnerSettings
from match_service.infrastructure.rooms.snapshot import RedisRoomStore
from match_service.infrastructure.rooms.stream import (
    RedisCommandBus,
    RedisPlayerRooms,
    RedisPresence,
)
from redis.asyncio import Redis

from gv_core.clock import SystemClock
from gv_core.ids import uuid7
from gv_core.random import SystemRandom
from gv_service.runners import supervise_runners
from gv_service.settings import RunnersSettings
from gv_testkit import InfraProvider
from gv_testkit.match import FakeLinkPort, FakeRealtime, FakeRoundPlanPort

pytestmark = pytest.mark.integration

WINDOW_S = 3  # минимум схемы
LEVELS = 2
ROUNDS = 3
POLL_MS = 2000  # верхняя граница сна: дедлайн должен будить раньше


def _fast(body: dict[str, Any]) -> dict[str, Any]:
    """Короткие окна, 2 уровня, отсчёт и ревил; 3 раунда (иначе тест шёл бы минутами)."""
    fast = copy.deepcopy(body)
    session = fast["session"]
    fast["clue"]["params"]["levels"] = fast["clue"]["params"]["levels"][:LEVELS]
    session["level_windows_s"] = [WINDOW_S] * LEVELS
    session["countdown_s"] = 1
    session["reveal_s"] = 1
    if "lobby" in session:
        # Схема: значение по умолчанию обязано быть среди переопределяемых.
        session["lobby"]["overridable"]["level_window_s"] = [WINDOW_S, 15]
    else:
        session["rounds"] = ROUNDS
    return fast


def _now_ms() -> int:
    return int(time.time() * 1000)


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    client = Redis.from_url(infra.redis_url(), socket_timeout=5)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


class Stack:
    """Один процесс ``rooms`` и клиенты комнаты."""

    def __init__(self, redis: Redis, key: str) -> None:
        self.redis = redis
        self.key = key
        self.bodies = {k: _fast(v) for k, v in seed_bodies().items()}
        self.factory = factory()
        self.store = RedisRoomStore(redis, snapshot_ttl_ms=3_600_000)
        self.bus = RedisCommandBus(redis, maxlen=1000)
        self.presence = RedisPresence(redis, ttl_s=600)
        self.realtime = FakeRealtime()
        self.records = MemoryRecords()
        owner = RoomOwner(
            factory=self.factory,
            plans=FakeRoundPlanPort(factory=plan_factory(self.bodies)),
            assets=FakeLinkPort(),
            records=self.records,
            store=self.store,
            player_rooms=RedisPlayerRooms(redis, ttl_ms=3_600_000),
            clock=SystemClock(),
            random=SystemRandom(),
            new_id=uuid7,
            seeds=lambda: uuid7().hex * 2,
            subscriptions=MemorySubscriptions(),
            settings=OwnerSettings(
                op_window=64, memory_limit=100, idle_ms=600_000, presence_timeout_ms=60_000
            ),
        )
        self.runner = RoomsRunner(
            redis,
            owner=owner,
            publisher=self.realtime,
            store=self.store,
            clock=SystemClock(),
            settings=RunnerSettings(
                instance="rooms-1",
                lease_ttl_ms=5000,
                renew_ms=1000,
                poll_ms=POLL_MS,
                max_rooms=10,
                scan_ms=200,
                snapshot_ttl_ms=3_600_000,
                closed_ttl_s=60,
                reply_ttl_s=30,
                presence_ttl_s=600,
            ),
        )
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(
            supervise_runners([self.runner], self.stop, RunnersSettings(stop_timeout_s=2))
        )

    async def room(self, host: UUID, **settings: Any) -> str:
        tpl = self.factory.template(self.key, 1, self.bodies[self.key])
        base = default_settings(tpl.template)
        if tpl.template.uses_collection:
            settings.setdefault("collection_ids", (uuid7(),))
        from dataclasses import replace

        code = "".join("ABCDEFGHJKMNPQRS"[b % 16] for b in uuid7().bytes[-8:])
        room = Room.create(
            code=code,
            mode=tpl.mode,
            template=tpl.template,
            settings=replace(base, **settings),
            host_id=host,
            nickname="host",
            now_ms=_now_ms(),
        )
        assert await self.store.create(room)
        return code

    async def send(self, code: str, user: UUID, kind: str, **data: Any) -> dict[str, Any]:
        command = RoomCommand(op_id=f"op-{uuid7().hex}", kind=kind, user_id=user, data=data)
        await self.bus.send(code, command)
        reply = await self.bus.wait_reply(code, command, 5000)
        assert reply is not None, (kind, data)
        return reply

    async def load(self, code: str) -> Room:
        room = await self.store.load(code)
        assert room is not None
        return room

    async def close(self) -> None:
        self.stop.set()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(self.task, 5)


async def _play(stack: Stack, players: list[UUID], code: str, style: str) -> list[float]:
    """Ходы по ``style`` на каждом уровне; задержки переходов после дедлайна (с).

    ``one_silent`` — пропуск, промах, молчание (уровень закрывается по таймеру);
    ``all_answer`` — все ответили (досрочное закрытие); ``all_silent`` — только таймеры.
    Лобби на 3 игроков из e2e CI: сервер доходит до итога сам — остановка была не здесь.
    """
    skipper, misser = players[0], players[1]
    handled: set[tuple[int, int]] = set()
    lateness: list[float] = []
    last_deadline: int | None = None
    async with asyncio.timeout(60):
        while True:
            now = _now_ms()
            for user in players:
                await stack.presence.touch(code, user, now)
            room = await stack.load(code)
            if room.phase is Phase.SUMMARY:
                return lateness
            match = room.match
            if room.phase is Phase.LEVEL_OPEN and match is not None and match.contest:
                key = (match.round_index, match.contest.level)
                if key not in handled:
                    if last_deadline is not None and key[1] > 1:
                        # Следующий уровень того же раунда — сразу по дедлайну окна.
                        lateness.append((now - last_deadline) / 1000)
                    handled.add(key)
                    turn = {
                        "round_index": key[0],
                        "level": key[1],
                        "expected_version": room.version,
                    }
                    if style != "all_silent":
                        skip = await stack.send(code, skipper, "skip", **turn)
                        assert skip["status"] == 200, skip
                        miss = await stack.send(
                            code, misser, "guess", **turn, answer={"text": "совсем не то"}
                        )
                        assert miss["status"] == 200, miss
                    if style == "all_answer":
                        third = await stack.send(code, players[2], "skip", **turn)
                        assert third["status"] == 200, third
                    last_deadline = match.contest.level_ends_at_ms
            await asyncio.sleep(0.05)


async def _wait_finished(stack: Stack, timeout_s: float = 10.0) -> None:
    """Дождаться записи итога в Postgres (двойник).

    Итог пишется после снимка (D8): runner сохраняет комнату в ``SUMMARY``, публикует,
    подтверждает команды, индексирует и только потом вызывает ``persist``. Тест видит
    ``SUMMARY`` по снимку и может прочитать записи раньше, чем они появятся (на медленном
    CI-раннере это случается), поэтому запись ждётся, а не читается сразу.
    """
    async with asyncio.timeout(timeout_s):
        while not stack.records.finished:  # noqa: ASYNC110 — у двойника записей нет события
            await asyncio.sleep(0.02)


@pytest.mark.parametrize(
    ("key", "style"),
    [
        ("film.lobby.collection", "one_silent"),
        ("film.party.frame", "one_silent"),
        ("film.lobby.collection", "all_answer"),
    ],
)
async def test_match_reaches_summary_on_timers_without_commands(
    redis: Redis, key: str, style: str
) -> None:
    stack = Stack(redis, key)
    try:
        host = uuid7()
        code = await stack.room(host, rounds=ROUNDS)  # число раундов — из плана
        others = [uuid7(), uuid7()]
        for i, user in enumerate(others):
            joined = await stack.send(code, user, "join", nickname=f"p{i}")
            assert joined["status"] == 200, joined
            ready = await stack.send(code, user, "ready", ready=True)
            assert ready["status"] == 200, ready
        version = (await stack.load(code)).version
        started = await stack.send(code, host, "start", expected_version=version)
        assert started["status"] == 200, started
        lateness = await _play(stack, [host, *others], code, style)
        room = await stack.load(code)
        assert room.match is not None and room.match.round_index == ROUNDS - 1
        await _wait_finished(stack)
        assert len(stack.records.finished) == 1
        # Runner просыпается к дедлайну, а не раз в poll_ms (2 с).
        if style != "all_answer":
            assert lateness and max(lateness) < 1.0, lateness
        stack.realtime.assert_versions_contiguous(f"room:{code}")
    finally:
        await stack.close()
