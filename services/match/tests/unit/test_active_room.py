"""Одна активная комната игрока: сервер сам освобождает устаревший указатель (D10, пп. 18, 28)."""

from __future__ import annotations

import pytest
from match_doubles import MemoryPresence
from match_harness import PRESENCE_TIMEOUT_MS, Harness
from match_service.application.owner import active_room_of, claim_room
from match_service.domain.errors import ACTIVE_ROOM_CONFLICT
from match_service.domain.model import Phase

from gv_core.errors import AppError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

STALE_MS = 25_000
RELEASE_MS = 90_000


async def _claim(h: Harness, user, code: str, presence: MemoryPresence | None) -> None:
    await claim_room(
        h.player_rooms,
        h.store,
        user,
        code,
        now_ms=h.now_ms(),
        pending_ms=10_000,
        presence=presence,
        stale_ms=STALE_MS,
    )


async def test_explicit_join_ignores_pointer_of_player_silent_longer_than_presence_timeout() -> (
    None
):
    """Закрытая вкладка: heartbeat пропал >25 с - новая комната не блокируется."""
    h = Harness()
    room, host = await h.room(code="AAAAAAAA")
    presence = MemoryPresence()
    await presence.touch(room.code, host, h.now_ms())
    h.clock.advance(seconds=STALE_MS / 1000 + 1)
    await _claim(h, host, "BBBBBBBB", presence)
    assert await h.player_rooms.get(host) == "BBBBBBBB"


async def test_fresh_heartbeat_still_blocks_second_room() -> None:
    h = Harness()
    room, host = await h.room(code="AAAAAAAA")
    presence = MemoryPresence()
    await presence.touch(room.code, host, h.now_ms())
    h.clock.advance(seconds=STALE_MS / 1000 - 5)
    with pytest.raises(AppError) as err:
        await _claim(h, host, "BBBBBBBB", presence)
    assert err.value.code == ACTIVE_ROOM_CONFLICT
    assert err.value.details == {"code": "AAAAAAAA", "phase": "lobby"}


async def test_without_presence_source_old_behaviour_is_kept() -> None:
    h = Harness()
    _, host = await h.room(code="AAAAAAAA")
    h.clock.advance(seconds=3600)
    with pytest.raises(AppError):
        await _claim(h, host, "BBBBBBBB", None)


async def test_active_room_reports_code_and_phase_or_nothing() -> None:
    h = Harness()
    room, host = await h.room(code="AAAAAAAA")
    presence = MemoryPresence()
    await presence.touch(room.code, host, h.now_ms())
    got = await active_room_of(
        h.player_rooms,
        h.store,
        host,
        now_ms=h.now_ms(),
        pending_ms=10_000,
        presence=presence,
        stale_ms=STALE_MS,
    )
    assert got is not None and (got.code, got.phase) == ("AAAAAAAA", "lobby")
    h.clock.advance(seconds=STALE_MS / 1000 + 1)  # игрок молчит - комнаты «нет»
    assert (
        await active_room_of(
            h.player_rooms,
            h.store,
            host,
            now_ms=h.now_ms(),
            pending_ms=10_000,
            presence=presence,
            stale_ms=STALE_MS,
        )
        is None
    )
    assert (
        await active_room_of(
            h.player_rooms,
            h.store,
            uuid7(),
            now_ms=h.now_ms(),
            pending_ms=10_000,
        )
        is None
    )


async def test_background_cleanup_releases_pointer_of_long_offline_player_in_lobby() -> None:
    h = Harness(presence_release_ms=RELEASE_MS)
    room, host = await h.room()
    guest = (await h.join(room, 1))[0]
    h.offline.add(guest)
    await h.tick(room, RELEASE_MS / 1000 - 5)
    assert await h.player_rooms.get(guest) == room.code
    await h.tick(room, 10)
    assert await h.player_rooms.get(guest) is None  # указатель освобождён
    assert await h.player_rooms.get(host) == room.code  # онлайн-хост не тронут
    assert room.phase is Phase.LOBBY


async def test_cleanup_never_frees_pointer_during_live_match() -> None:
    """Идущий матч держит игрока: выбывание решает grace матча, а не фоновая чистка."""
    h = Harness(presence_release_ms=RELEASE_MS)
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    await h.start(room, host)
    h.offline.add(guest)
    await h.tick(room, PRESENCE_TIMEOUT_MS / 1000 + 1)
    assert room.phase is not Phase.LOBBY
    assert await h.player_rooms.get(guest) == room.code


@pytest.mark.parametrize("stage", ["lobby", "level", "summary_closed"])
async def test_explicit_leave_works_from_any_state_and_frees_pointer(stage: str) -> None:
    h = Harness()
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    if stage != "lobby":
        await h.start(room, host)
    if stage == "summary_closed":
        await h.ok(room, guest, "leave")
        await h.ok(room, host, "leave")
        assert room.is_closed or room.phase is Phase.SUMMARY
    else:
        await h.ok(room, guest, "leave")
    assert await h.player_rooms.get(guest) is None


async def test_leave_of_closed_room_releases_pointer_and_reports_reason() -> None:
    h = Harness()
    room, _host = await h.room()
    (guest,) = await h.join(room, 1)
    room.close("idle", h.now_ms())
    await h.player_rooms.assign(guest, room.code, h.now_ms())  # указатель пережил закрытие
    reply = await h.cmd(room, guest, "leave")
    assert reply["status"] == 404 and reply["details"] == {"reason": "idle"}
    assert await h.player_rooms.get(guest) is None


async def test_leave_without_seat_frees_pointer_idempotently() -> None:
    """Вход не завершился (места нет), указатель есть: leave освобождает, а не отвечает 403."""
    h = Harness()
    room, _ = await h.room()
    stray = uuid7()
    await h.player_rooms.assign(stray, room.code, h.now_ms())
    first = await h.cmd(room, stray, "leave")
    again = await h.cmd(room, stray, "leave")
    assert first["status"] == 200 and again["status"] == 200
    assert await h.player_rooms.get(stray) is None


async def test_unavailable_presence_source_falls_back_to_old_rule() -> None:
    class Broken:
        async def touch(self, code: str, user_id, now_ms: int) -> None: ...

        async def seen(self, code: str):
            raise OSError("redis down")

    h = Harness()
    _room, host = await h.room(code="AAAAAAAA")
    h.clock.advance(seconds=3600)
    with pytest.raises(AppError) as err:
        await _claim(h, host, "BBBBBBBB", Broken())  # type: ignore[arg-type]
    assert err.value.code == ACTIVE_ROOM_CONFLICT


async def test_silent_player_in_live_match_still_holds_the_room() -> None:
    """Идущий матч не отпускает молчащего: его судьбу решают пауза и grace, а не явный вход."""
    h = Harness()
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    await h.start(room, host)
    presence = MemoryPresence()
    await presence.touch(room.code, guest, h.now_ms())
    h.clock.advance(seconds=STALE_MS / 1000 + 5)
    assert room.phase is not Phase.LOBBY
    with pytest.raises(AppError) as err:
        await _claim(h, guest, "BBBBBBBB", presence)
    assert err.value.code == ACTIVE_ROOM_CONFLICT
