"""Пустое лобби закрывается локально по таймеру владельца, шина не нужна (пункт 2 хвоста 4a)."""

from __future__ import annotations

import pytest
from match_harness import IDLE_MS, Harness
from match_service.domain.model import Phase

pytestmark = pytest.mark.unit

EMPTY_MS = 120_000


async def test_lobby_with_everybody_offline_closes_after_empty_timeout() -> None:
    h = Harness(empty_lobby_ms=EMPTY_MS)
    room, host = await h.room()
    h.offline.add(host)
    await h.tick(room, EMPTY_MS / 1000 - 5)
    assert room.phase is Phase.LOBBY  # таймер ещё не вышел
    await h.tick(room, 6)
    assert room.phase is Phase.CLOSED and room.close_reason == "empty_timeout"
    assert EMPTY_MS < IDLE_MS  # раньше общего простоя лобби


async def test_returning_player_keeps_the_lobby() -> None:
    h = Harness(empty_lobby_ms=EMPTY_MS)
    room, host = await h.room()
    h.offline.add(host)
    await h.tick(room, EMPTY_MS / 1000 - 5)
    h.offline.discard(host)  # heartbeat вернулся
    await h.tick(room, 10)
    await h.tick(room, EMPTY_MS / 1000 - 5)
    assert room.phase is Phase.LOBBY


async def test_one_online_player_is_enough() -> None:
    h = Harness(empty_lobby_ms=EMPTY_MS)
    room, host = await h.room()
    await h.join(room, 1)
    h.offline.add(host)
    await h.tick(room, EMPTY_MS / 1000 + 10)
    assert room.phase is Phase.LOBBY


async def test_disabled_by_zero() -> None:
    h = Harness(empty_lobby_ms=0)
    room, host = await h.room()
    h.offline.add(host)
    await h.tick(room, EMPTY_MS / 1000 * 3)
    assert room.phase is Phase.LOBBY


async def test_closes_with_bus_and_centrifugo_down_and_closed_is_not_lost() -> None:
    """Postgres/outbox и Centrifugo недоступны: закрытие - в состоянии комнаты, не в шине."""
    h = Harness(empty_lobby_ms=EMPTY_MS)
    h.records.fail = True
    h.subscriptions.fail = True
    h.subscriptions.fail_disconnect = True
    room, host = await h.room()
    h.offline.add(host)
    await h.tick(room, EMPTY_MS / 1000 + 1)
    assert room.phase is Phase.CLOSED
    changes = room.pull_changes()  # дельта closed ждёт публикации runner-ом после снимка
    assert any(c.kind == "closed" for c in changes)
    assert await h.player_rooms.get(host) is None  # указатель игрока освобождён
    assert not h.records.started and not h.records.finished  # матча не было - писать нечего
