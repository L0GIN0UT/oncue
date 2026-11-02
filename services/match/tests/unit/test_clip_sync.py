"""Общий старт таймера уровня: подтверждение «фрагмент загружен» (пункт 24 замечаний)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from match_harness import Harness
from match_service.domain.model import Phase
from match_service.domain.room import Room

pytestmark = pytest.mark.unit

WAIT_MS = 10_000


async def _duel(
    h: Harness, *, capable: tuple[bool, bool] = (True, True)
) -> tuple[Room, UUID, UUID]:
    """Дуэль на уровне 1 раунда 0; ``capable`` - кто объявил поддержку подтверждений."""
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    for user, flag in zip((host, guest), capable, strict=True):
        if flag:
            await h.ok(room, user, "clip_ready")  # без раунда/уровня: объявление поддержки
    await h.start(room, host)
    assert room.phase is Phase.LEVEL_OPEN
    return room, host, guest


def _level(room: Room) -> tuple[int, int]:
    assert room.match is not None and room.match.contest is not None
    return room.match.round_index, room.match.contest.level


async def _ready(h: Harness, room: Room, user: UUID) -> dict[str, Any]:
    round_index, level = _level(room)
    return await h.ok(room, user, "clip_ready", round_index=round_index, level=level)


def _kinds(room: Room) -> list[str]:
    return [c.kind for c in room.changes]


async def test_capability_declaration_marks_the_seat() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host = await h.room()
    assert room.seat_of(host).clip_sync is False
    await h.ok(room, host, "clip_ready")
    assert room.seat_of(host).clip_sync is True


async def test_timer_waits_for_everyone_and_starts_at_the_common_signal() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h)
    assert room.match is not None and room.match.clip_gate is not None
    ends_before = room.phase_ends_at_ms
    assert ends_before is not None
    await h.tick(room, 6)  # фрагмент грузится, таймер стоит
    assert room.phase is Phase.LEVEL_OPEN and room.match.clip_gate is not None
    await _ready(h, room, host)
    assert room.match.clip_gate is not None  # соперник ещё грузит
    room.pull_changes()
    await h.tick(room, 2)
    await _ready(h, room, guest)
    assert room.match.clip_gate is None
    assert "level_started" in _kinds(room)
    assert room.phase_ends_at_ms is not None
    # Окно сдвинулось на простой (6 + 2 с): у обоих отсчёт идёт с одного момента.
    assert room.phase_ends_at_ms - ends_before >= 8_000


async def test_silent_capable_player_releases_gate_after_max_wait() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, _guest = await _duel(h)
    ends_before = room.phase_ends_at_ms
    await _ready(h, room, host)
    await h.tick(room, WAIT_MS / 1000 - 2)
    assert room.match is not None and room.match.clip_gate is not None
    await h.tick(room, 3)
    assert room.match.clip_gate is None
    assert room.phase_ends_at_ms is not None and ends_before is not None
    assert room.phase_ends_at_ms - ends_before >= WAIT_MS


async def test_disconnected_player_is_not_waited_for() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h)
    await _ready(h, room, host)
    h.offline.add(guest)
    await h.tick(room, 13)  # presence_timeout: соперник отключён, затвор его не ждёт
    assert room.match is not None and room.match.clip_gate is None


async def test_old_client_without_capability_never_delays_the_timer() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, _host, _guest = await _duel(h, capable=(False, False))
    assert room.match is not None and room.match.clip_gate is None
    assert "level_started" not in _kinds(room)


async def test_mixed_room_with_old_client_never_closes_the_gate() -> None:
    """Старая вкладка не знает про awaiting_clips: затвор ей не закрываем, ответы как раньше."""
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h, capable=(True, False))
    assert room.match is not None and room.match.clip_gate is None
    assert "level_started" not in _kinds(room)
    reply = await h.cmd(room, guest, "guess", **h.guess_data(room, "что-то"))
    assert reply["status"] != 409
    del host


async def test_duplicate_confirmation_is_harmless() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h)
    await _ready(h, room, host)
    await _ready(h, room, host)
    assert room.match is not None and room.match.clip_gate is not None
    assert room.match.clip_gate.ready == (host,)
    await _ready(h, room, guest)
    await _ready(h, room, guest)  # после открытия затвора - тоже без ошибки
    assert room.match.clip_gate is None


async def test_leaving_player_does_not_hold_the_gate() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h)
    await _ready(h, room, host)
    await h.ok(room, guest, "leave")
    await h.tick(room, 0)
    assert room.match is None or room.match.clip_gate is None


async def test_disconnect_during_gate_keeps_the_whole_level_window_after_resume() -> None:
    """Пауза дуэли считает остаток от настоящего окна: простой загрузки не съедает время."""
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h)
    assert room.phase_ends_at_ms is not None
    window_ms = room.phase_ends_at_ms - h.now_ms()
    await _ready(h, room, host)
    h.offline.add(guest)
    await h.tick(room, 13)  # отключение -> затвор открыт принудительно -> пауза
    assert room.phase is Phase.PAUSED
    h.offline.discard(guest)
    await h.tick(room, 1)
    assert room.phase is Phase.LEVEL_OPEN
    assert room.phase_ends_at_ms is not None
    assert abs((room.phase_ends_at_ms - h.now_ms()) - window_ms) <= 2_000


async def test_gate_restored_from_snapshot_still_opens_at_deadline() -> None:
    from match_service.infrastructure.rooms.codec import dumps, loads

    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, _guest = await _duel(h)
    await _ready(h, room, host)
    restored = loads(dumps(room))
    h2 = Harness(clock=h.clock, bodies=h.bodies, clip_ready_max_wait_ms=WAIT_MS)
    await h2.tick(restored, WAIT_MS / 1000 + 1)
    assert restored.match is not None and restored.match.clip_gate is None
    assert "level_started" in [c.kind for c in restored.changes]


async def test_gate_disabled_by_zero_wait() -> None:
    h = Harness(clip_ready_max_wait_ms=0)
    room, _host, _guest = await _duel(h)
    assert room.match is not None and room.match.clip_gate is None


async def test_stale_confirmation_changes_nothing() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, _guest = await _duel(h)
    round_index, level = _level(room)
    await h.ok(room, host, "clip_ready", round_index=round_index, level=level + 1)
    assert room.match is not None and room.match.clip_gate is not None
    assert room.match.clip_gate.ready == ()


async def test_guess_is_refused_while_clips_are_loading() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, _guest = await _duel(h)
    reply = await h.cmd(room, host, "guess", **h.guess_data(room, "что-то"))
    assert reply["status"] == 409 and reply["code"] == "match.phase_mismatch"


async def test_next_level_gets_its_own_gate() -> None:
    h = Harness(clip_ready_max_wait_ms=WAIT_MS)
    room, host, guest = await _duel(h)
    await _ready(h, room, host)
    await _ready(h, room, guest)
    await h.ok(room, host, "skip", **h.turn(room))
    await h.ok(room, guest, "skip", **h.turn(room))
    await h.tick(room, 1)
    assert room.phase is Phase.LEVEL_OPEN
    assert room.match is not None and room.match.contest is not None
    assert room.match.contest.level == 2
    assert room.match.clip_gate is not None and room.match.clip_gate.level == 2
