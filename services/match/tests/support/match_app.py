"""Сборка сервиса match для component-тестов: api + процесс rooms, Postgres и Redis.

Другие сервисы — фейки ``gv_testkit.match`` (план game, ссылки media-pipeline, Centrifugo);
шаблоны режимов — сиды ``seeds/game/modes``; часы — ``FakeClock`` (владелец комнат читает
дедлайны по ним, цикл просыпается каждые ``ROOMS_POLL_MS``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from match_service.application.ports import RoomCommand
from match_service.infrastructure.db.tables import TABLES
from match_service.infrastructure.rooms.stream import RedisCommandBus
from match_service.settings import MatchSettings, Settings
from pydantic import SecretStr
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Outcome
from gv_service import Infrastructure, RealtimeSettings
from gv_testkit import (
    FakeClock,
    TestTokenIssuer,
    load_example,
    outbox_envelopes,
)
from gv_testkit.match import FakeLinkPort, FakeRealtime, FakeRoundPlanPort

START = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SUB_SECRET = "s" * 40
ORIGIN = "https://oncue.test"
SYNC_USER = UUID(int=1)
SYNC_TIMEOUT_MS = 3000


class MemoryModes:
    """``ModeSource``: опубликованные шаблоны версии 1 (проекция ``mode_heads`` не нужна)."""

    def __init__(self, bodies: dict[str, dict[str, Any]]) -> None:
        self.bodies = bodies

    async def current(self, key: str) -> int | None:
        return 1 if key in self.bodies else None

    async def body(self, key: str, version: int) -> dict[str, Any] | None:
        return self.bodies.get(key) if version == 1 else None


@dataclass
class Player:
    """Пользователь теста."""

    user_id: UUID
    headers: dict[str, str]
    ops: int = 0

    def op(self) -> str:
        self.ops += 1
        return f"op-{self.user_id.hex[-12:]}-{self.ops:04d}"


@dataclass
class MatchApp:
    """Сервис match и его фейки."""

    client: httpx.AsyncClient
    infra: Infrastructure
    clock: FakeClock
    issuer: TestTokenIssuer
    plans: FakeRoundPlanPort
    links: FakeLinkPort
    realtime: FakeRealtime
    processor: EventProcessor
    redis: Redis
    subscriptions: Any = None
    online: set[UUID] = field(default_factory=set)

    # ------------------------------------------------------------------ субъекты
    def user(self, *, sanctions: int = 0, perms: Iterable[str] = ()) -> Player:
        user_id = uuid7()
        token = self.issuer.issue(user_id=user_id, sanctions=sanctions, perms=list(perms))
        return Player(user_id, {"Authorization": f"Bearer {token}"})

    def guest(self) -> Player:
        guest_id = uuid7()
        token = self.issuer.issue_guest(guest_id=guest_id)
        return Player(guest_id, {"Authorization": f"Bearer {token}"})

    async def nickname(self, player: Player, nickname: str) -> None:
        """Профиль игрока в проекции (``identity.UserRegistered``)."""
        base = load_example("identity.UserRegistered")
        payload = {
            **base.payload,
            "user_id": str(player.user_id),
            "nickname": nickname,
            "version": 1,
        }
        envelope = base.model_copy(
            update={"aggregate_id": str(player.user_id), "payload": payload, "event_id": uuid7()}
        )
        assert await self.deliver(envelope) is Outcome.PROCESSED

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    # ------------------------------------------------------------------ запросы
    async def create(self, host: Player, mode_key: str, **body: Any) -> dict[str, Any]:
        response = await self.client.post(
            "/v1/rooms",
            json={"mode_key": mode_key, **body},
            headers={**host.headers, "Idempotency-Key": host.op()},
        )
        assert response.status_code == 201, response.text
        self.online.add(host.user_id)
        data: dict[str, Any] = response.json()
        return data

    async def post(self, player: Player, code: str, path: str, **body: Any) -> httpx.Response:
        """Команда комнаты (``op_id`` — новый, если не задан)."""
        body.setdefault("op_id", player.op())
        return await self.client.post(f"/v1/rooms/{code}/{path}", json=body, headers=player.headers)

    async def ok(self, player: Player, code: str, path: str, **body: Any) -> dict[str, Any]:
        response = await self.post(player, code, path, **body)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def join(self, code: str, *players: Player, ready: bool = True) -> None:
        for player in players:
            await self.ok(player, code, "join")
            self.online.add(player.user_id)
            if ready:
                await self.ok(player, code, "ready", ready=True)

    async def state(self, player: Player, code: str) -> dict[str, Any]:
        response = await self.client.get(f"/v1/rooms/{code}/state", headers=player.headers)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def tick(self, player: Player, code: str, known: int | None = None) -> httpx.Response:
        return await self.client.post(
            f"/v1/rooms/{code}/tick", json={"known_version": known}, headers=player.headers
        )

    # ------------------------------------------------------------------ время
    async def heartbeat(self, code: str, players: Iterable[Player]) -> None:
        for player in players:
            if player.user_id in self.online:
                response = await self.tick(player, code)
                assert response.status_code in (200, 403, 404), response.text

    async def advance(self, code: str, players: Iterable[Player], seconds: float) -> None:
        """Сдвинуть часы; игроки на связи шлют heartbeat; дождаться итерации владельца."""
        players = list(players)
        self.clock.advance(seconds=seconds)
        await self.heartbeat(code, players)
        await self.sync(code)

    async def sync(self, code: str) -> None:
        """Барьер: ответ на служебную команду приходит после ``tick`` той же итерации.

        Неизвестная команда отвергается владельцем без изменений комнаты, но ответ
        отправляется только после снимка итерации, где уже отработаны дедлайны по
        сдвинутым часам. Закрытая комната не отвечает — тогда ждать нечего.
        """
        command = RoomCommand(op_id=f"sync-{uuid7().hex}", kind="sync", user_id=SYNC_USER, data={})
        bus = RedisCommandBus(self.redis, maxlen=10_000)
        await bus.send(code, command)
        await bus.wait_reply(code, command, SYNC_TIMEOUT_MS)

    async def until(
        self,
        player: Player,
        code: str,
        check: Callable[[dict[str, Any]], bool],
        timeout: float = 5,  # noqa: ASYNC109 — общий предел ожидания сценария
    ) -> dict[str, Any]:
        """Опрос ``GET /state`` до выполнения условия."""
        async with asyncio.timeout(timeout):
            while True:
                state = await self.state(player, code)
                if check(state):
                    return state
                await asyncio.sleep(0.02)

    async def phase(
        self,
        player: Player,
        code: str,
        *phases: str,
        timeout: float = 5,  # noqa: ASYNC109 — общий предел ожидания сценария
    ) -> dict[str, Any]:
        return await self.until(player, code, lambda s: s["room"]["phase"] in phases, timeout)

    async def outbox(self) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await outbox_envelopes(session, TABLES.outbox)


def settings(database_url: str, redis_url: str, **match: Any) -> Settings:
    defaults: dict[str, Any] = {
        "rooms_poll_ms": 10,
        "lease_ttl_s": 2,
        "lease_renew_s": 0.5,
        "orphan_scan_s": 0.2,
        "lobby_create_per_user_per_hour": 1000,
        "join_by_code_per_ip_per_min": 10_000,
        "commands_per_player_per_s": 1000,
        "chat_per_player_per_min": 1000,
        # Сценарии опрашивают /state и /tick в реальном времени при стоящих FakeClock.
        "tick_per_player_per_min": 100_000,
        "state_per_player_per_min": 100_000,
        "join_by_code_per_user_per_min": 10_000,
        "emote_per_player_per_min": 1000,
        "presence_timeout_s": 120,
    }
    return Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        public_origins=(ORIGIN,),
        match=MatchSettings(**(defaults | match)),
        realtime=RealtimeSettings(subscription_token_secret=SecretStr(SUB_SECRET)),
    )
