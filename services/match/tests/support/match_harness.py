"""Сборка владельца комнаты на двойниках для unit-тестов (без Redis и HTTP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from match_doubles import (
    START,
    MemoryPlayerRooms,
    MemoryRecords,
    MemoryStore,
    MemorySubscriptions,
    factory,
    plan_factory,
    seed_bodies,
)
from match_service.application.owner import OwnerSettings, RoomOwner
from match_service.application.ports import RoomCommand
from match_service.domain.model import Phase
from match_service.domain.room import Room, SettingsChange, apply_settings, default_settings

from gv_core.ids import uuid7
from gv_testkit import FakeClock, FakeRandom
from gv_testkit.match import FakeLinkPort, FakeRoundPlanPort

PRESENCE_TIMEOUT_MS = 12_000
IDLE_MS = 30 * 60_000


@dataclass
class Harness:
    """Владелец комнаты, часы и двойники портов."""

    clock: FakeClock = field(default_factory=lambda: FakeClock(START))
    bodies: dict[str, dict[str, Any]] = field(default_factory=seed_bodies)
    assets: FakeLinkPort = field(default_factory=FakeLinkPort)
    store: MemoryStore = field(default_factory=MemoryStore)
    player_rooms: MemoryPlayerRooms = field(default_factory=MemoryPlayerRooms)
    records: MemoryRecords = field(default_factory=MemoryRecords)
    subscriptions: MemorySubscriptions = field(default_factory=MemorySubscriptions)
    seeds: list[str] = field(default_factory=list)
    offline: set[UUID] = field(default_factory=set)
    seen: dict[UUID, int] = field(default_factory=dict)
    op_window: int = 64
    poison_limit: int = 3
    op_users_max: int = 16
    empty_lobby_ms: int = 0
    artwork_sets: bool = False
    artwork_refresh_margin_ms: int = 10_000
    _ops: int = 0

    def __post_init__(self) -> None:
        self.plans = FakeRoundPlanPort(factory=plan_factory(self.bodies, artwork=self.artwork_sets))
        self.factory = factory()
        self.owner = RoomOwner(
            factory=self.factory,
            plans=self.plans,
            assets=self.assets,
            records=self.records,
            store=self.store,
            player_rooms=self.player_rooms,
            clock=self.clock,
            random=FakeRandom(7),
            new_id=self._new_id,
            seeds=self._seed,
            subscriptions=self.subscriptions,
            settings=OwnerSettings(
                op_window=self.op_window,
                memory_limit=500,
                idle_ms=IDLE_MS,
                presence_timeout_ms=PRESENCE_TIMEOUT_MS,
                empty_lobby_ms=self.empty_lobby_ms,
                poison_limit=self.poison_limit,
                op_users_max=self.op_users_max,
                artwork_refresh_margin_ms=self.artwork_refresh_margin_ms,
            ),
        )

    fixed_id: UUID | None = None

    def _new_id(self) -> UUID:
        return self.fixed_id or uuid7()

    def _seed(self) -> str:
        seed = uuid7().hex + uuid7().hex
        self.seeds.append(seed)
        return seed

    def now_ms(self) -> int:
        return int(self.clock.now().timestamp() * 1000)

    async def room(
        self, key: str = "music.duel.classic", *, code: str = "K7MQ2ZXA", **change: Any
    ) -> tuple[Room, UUID]:
        """Комната шаблона ``key``; возвращает комнату и хоста."""
        tpl = self.factory.template(key, 1, self.bodies[key])
        settings = apply_settings(
            tpl.template, default_settings(tpl.template), SettingsChange(**change)
        )
        host = uuid7()
        room = Room.create(
            code=code,
            mode=tpl.mode,
            template=tpl.template,
            settings=settings,
            host_id=host,
            nickname="host",
            now_ms=self.now_ms(),
        )
        await self.store.create(room)
        await self.player_rooms.claim(host, code, self.now_ms())
        return room, host

    async def cmd(
        self, room: Room, user: UUID, command: str, *, op_id: str | None = None, **data: Any
    ) -> dict[str, Any]:
        """Команда владельцу (новый ``op_id`` на каждый вызов)."""
        self._ops += 1
        cmd = RoomCommand(op_id=op_id or f"op-{uuid7().hex}", kind=command, user_id=user, data=data)
        reply = await self.owner.handle(room, cmd)
        await self.owner.persist(room)  # как runner: после снимка
        return reply

    async def ok(self, room: Room, user: UUID, command: str, **data: Any) -> dict[str, Any]:
        """Команда, которая должна пройти; тело ответа."""
        reply = await self.cmd(room, user, command, **data)
        assert reply["status"] == 200, reply
        body: dict[str, Any] = reply["body"]
        return body

    async def join(self, room: Room, n: int) -> list[UUID]:
        """``n`` новых игроков входят и отмечают готовность."""
        users = []
        for i in range(n):
            user = uuid7()
            await self.ok(room, user, "join", nickname=f"p{i}")
            await self.ok(room, user, "ready", ready=True)
            users.append(user)
        return users

    async def tick(self, room: Room, seconds: float = 0) -> None:
        """Сдвинуть часы; игроки вне ``offline`` шлют heartbeat."""
        self.clock.advance(seconds=seconds)
        now = self.now_ms()
        for seat in room.seats:
            if seat.user_id not in self.offline:
                self.seen[seat.user_id] = now
        await self.owner.tick(room, dict(self.seen))
        await self.owner.persist(room)

    async def until(self, room: Room, *phases: Phase, limit_s: int = 600) -> None:
        """Идти по секунде, пока комната не окажется в одной из ``phases``."""
        for _ in range(limit_s):
            if room.phase in phases:
                return
            await self.tick(room, 1)
        raise AssertionError(f"комната в {room.phase}, ждали {phases}")

    async def start(self, room: Room, host: UUID) -> None:
        """Старт и конец отсчёта: открыт уровень 1 раунда 0."""
        await self.ok(room, host, "start", expected_version=room.version)
        await self.tick(room, 3)

    def guess_data(self, room: Room, text: str) -> dict[str, Any]:
        match = room.match
        assert match is not None and match.contest is not None
        return {
            "round_index": match.round_index,
            "level": match.contest.level,
            "expected_version": room.version,
            "answer": {"text": text},
        }

    def turn(self, room: Room) -> dict[str, Any]:
        """Поля команды фазы (пас): раунд, уровень, версия."""
        data = self.guess_data(room, "")
        del data["answer"]
        return data

    def title(self, room: Room) -> str:
        assert room.match is not None
        return room.match.current.answer.title
