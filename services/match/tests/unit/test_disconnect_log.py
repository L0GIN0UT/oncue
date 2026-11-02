"""Причины и тайминги отключений видны в логе (пункт 31 замечаний тестирования)."""

from __future__ import annotations

import pytest
from match_harness import PRESENCE_TIMEOUT_MS, Harness
from match_service.domain.model import Presence
from structlog.testing import capture_logs

pytestmark = pytest.mark.unit


def _events(logs: list[dict], name: str) -> list[dict]:
    return [entry for entry in logs if entry["event"] == name]


async def test_presence_timeout_is_logged_with_timing() -> None:
    h = Harness()
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    h.offline.add(guest)
    with capture_logs() as logs:
        await h.tick(room, PRESENCE_TIMEOUT_MS / 1000 + 1)
    (entry,) = _events(logs, "match.player_disconnected")
    assert entry["reason"] == "presence_timeout"
    assert entry["user_id"] == str(guest) and entry["room"] == room.code
    assert entry["silent_s"] >= PRESENCE_TIMEOUT_MS // 1000
    assert entry["timeout_s"] == PRESENCE_TIMEOUT_MS // 1000
    assert entry["phase"] == "lobby"
    assert room.seat_of(guest).presence is Presence.DISCONNECTED
    assert host not in {e["user_id"] for e in _events(logs, "match.player_disconnected")}


async def test_reconnect_is_logged_with_offline_duration_and_disconnect_once() -> None:
    h = Harness()
    room, _ = await h.room()
    (guest,) = await h.join(room, 1)
    h.offline.add(guest)
    with capture_logs() as logs:
        await h.tick(room, PRESENCE_TIMEOUT_MS / 1000 + 1)
        await h.tick(room, 5)  # всё ещё нет: повторного «отключён» быть не должно
        h.offline.discard(guest)
        await h.tick(room, 5)
    assert len(_events(logs, "match.player_disconnected")) == 1
    (back,) = _events(logs, "match.player_reconnected")
    assert back["user_id"] == str(guest) and back["offline_s"] >= 5
