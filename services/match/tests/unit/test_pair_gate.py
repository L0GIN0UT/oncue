"""Найденная пара: ворота первого сигнала и «не явился» без штрафа (пункты 60, 61)."""

from __future__ import annotations

from uuid import UUID

import pytest
from match_harness import PRESENCE_TIMEOUT_MS, Harness
from match_service.application.views import _player_delta
from match_service.domain.model import Phase, Presence
from structlog.testing import capture_logs

pytestmark = pytest.mark.unit

GRACE_S = 30


async def _countdown(h: Harness) -> tuple:
    """Комната в отсчёте с двумя игроками и воротами пары (как у матчера)."""
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    room.pair_deadline_ms = h.now_ms() + GRACE_S * 1000
    await h.ok(room, host, "start", expected_version=room.version)
    assert room.phase is Phase.COUNTDOWN
    return room, host, guest


def _silent(h: Harness, *users: UUID) -> None:
    """Игрок ни разу не подал сигнал: в ``seen`` его нет."""
    h.offline.update(users)


async def test_countdown_waits_for_first_signal_of_both_players() -> None:
    h = Harness()
    room, _, guest = await _countdown(h)
    _silent(h, guest)
    await h.tick(room, 10)  # отсчёт (3 с) давно прошёл, но второй не подал сигнал
    assert room.phase is Phase.COUNTDOWN and room.match.started_at_ms is None
    assert room.pair_deadline_ms is not None


async def test_gate_opens_and_countdown_restarts_once_both_signalled() -> None:
    h = Harness()
    room, _, guest = await _countdown(h)
    _silent(h, guest)
    await h.tick(room, 10)
    h.offline.discard(guest)
    await h.tick(room, 1)  # оба на связи: ворота открыты
    assert room.pair_deadline_ms is None
    assert room.phase is Phase.COUNTDOWN  # игроки видят полный отсчёт, а не мгновенный старт
    await h.tick(room, 4)
    assert room.phase is Phase.LEVEL_OPEN


async def test_presence_is_not_counted_while_pair_gate_waits() -> None:
    h = Harness()
    room, host, guest = await _countdown(h)
    _silent(h, guest)
    with capture_logs() as logs:
        await h.tick(room, PRESENCE_TIMEOUT_MS / 1000 + 1)
    assert not [e for e in logs if e["event"] == "match.player_disconnected"]
    assert room.seat_of(guest).presence is Presence.ONLINE
    assert room.seat_of(host).presence is Presence.ONLINE


async def test_no_show_closes_room_without_match_record_or_penalty() -> None:
    h = Harness()
    room, host, guest = await _countdown(h)
    _silent(h, guest)
    with capture_logs() as logs:
        await h.tick(room, GRACE_S + 1)
    assert room.is_closed and room.close_reason == "no_show"
    assert room.match is not None and room.match.finish is None  # техпоражения нет
    assert room.match.started_at_ms is None
    assert not h.records.finished and not h.records.started  # матч не записан
    (entry,) = [e for e in logs if e["event"] == "match.ranked_no_show"]
    assert entry["absent"] == [str(guest)] and entry["survivors"] == [str(host)]


async def test_no_show_hands_survivor_to_ranked_hooks_for_requeue() -> None:
    h = Harness()
    calls: list[tuple[str, list[UUID]]] = []

    class Hooks:
        async def started(self, room: object) -> None:  # pragma: no cover - не вызывается
            raise AssertionError("матч не стартовал")

        async def aborted(self, room: object, survivors: list[UUID]) -> None:
            calls.append((room.close_reason, list(survivors)))  # type: ignore[attr-defined]

    h.owner._ranked = Hooks()  # подмена порта владельца
    room, host, guest = await _countdown(h)
    _silent(h, guest)
    await h.tick(room, GRACE_S + 1)
    assert calls == [("no_show", [host])]


async def test_everyone_present_before_deadline_is_not_a_no_show() -> None:
    h = Harness()
    room, _, _ = await _countdown(h)
    await h.tick(room, GRACE_S - 5)
    assert not room.is_closed and room.pair_deadline_ms is None


async def test_reconnect_publishes_player_delta_with_online_presence() -> None:
    """Пункт 61: возврат игрока рассылается дельтой ``player`` с ``presence=online``."""
    h = Harness()
    room, _ = await h.room()
    (guest,) = await h.join(room, 1)
    h.offline.add(guest)
    await h.tick(room, PRESENCE_TIMEOUT_MS / 1000 + 1)
    went = [c for c in room.pull_changes() if c.kind == "player"]
    assert went and _player_delta(room, str(guest))["presence"] == "disconnected"
    h.offline.discard(guest)
    await h.tick(room, 1)
    back = [c for c in room.pull_changes() if c.kind == "player"]
    assert [c.data["user_id"] for c in back] == [str(guest)]
    delta = _player_delta(room, str(guest))
    assert delta["presence"] == "online"
