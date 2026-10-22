"""Сборка владельца комнаты на двойниках для unit-тестов (без Redis и HTTP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from match_doubles import (
    START,
    FakeAssets,
    FakePlans,
    MemoryPlayerRooms,
    MemoryRecords,
    MemoryStore,
    duel_body,
    factory,
    lobby_body,
    party_body,
)
from match_service.application.owner import OwnerSettings, RoomOwner
from match_service.application.ports import RoomCommand
from match_service.domain.model import Phase
from match_service.domain.room import Room, SettingsChange, apply_settings, default_settings

from gv_core.ids import uuid7
from gv_testkit import FakeClock, FakeRandom

PRESENCE_TIMEOUT_MS = 12_000
IDLE_MS = 30 * 60_000


@dataclass
class Harness:
    """Владелец комнаты, часы и двойники портов."""

    clock: FakeClock = field(default_factory=lambda: FakeClock(START))
    plans: FakePlans = field(
        default_factory=lambda: FakePlans(
            {
                "music.duel.classic": duel_body(),
                "film.party.frame": party_body(),
                "film.lobby.collection": lobby_body(),
            }
        )
    )
    assets: FakeAssets = field(default_factory=FakeAssets)
    store: MemoryStore = field(default_factory=MemoryStore)
    player_rooms: MemoryPlayerRooms = field(default_factory=MemoryPlayerRooms)
    records: MemoryRecords = field(default_factory=MemoryRecords)
    offline: set[UUID] = field(default_factory=set)
    seen: dict[UUID, int] = field(default_factory=dict)
    _ops: int = 0

    def __post_init__(self) -> None:
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
            new_id=uuid7,
            settings=OwnerSettings(
                op_window=64,
                memory_limit=500,
                idle_ms=IDLE_MS,
                presence_timeout_ms=PRESENCE_TIMEOUT_MS,
            ),
        )

    def now_ms(self) -> int:
        return int(self.clock.now().timestamp() * 1000)

    async def room(
        self, key: str = "music.duel.classic", *, code: str = "K7MQ2ZXA", **change: Any
    ) -> tuple[Room, UUID]:
        """Комната шаблона ``key``; возвращает комнату и хоста."""
        tpl = self.factory.template(key, 1, self.plans.bodies[key])
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
        await self.player_rooms.claim(host, code)
        return room, host

    async def cmd(
        self, room: Room, user: UUID, command: str, *, op_id: str | None = None, **data: Any
    ) -> dict[str, Any]:
        """Команда владельцу (новый ``op_id`` на каждый вызов)."""
        self._ops += 1
        cmd = RoomCommand(
            op_id=op_id or f"op-{self._ops:08d}", kind=command, user_id=user, data=data
        )
        return await self.owner.handle(room, cmd)

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

    def title(self, room: Room) -> str:
        assert room.match is not None
        return room.match.current.answer.title
