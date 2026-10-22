"""Аудит безопасности и код-ревью match (этап 4a): каждая находка воспроизведена тестом.

Номера в именах и комментариях — пункты аудита (A1–A12) и ревью (B1–B4, R5–R13, R9).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from match_harness import Harness
from match_service.application.ports import RoomCommand
from match_service.application.views import match_view, messages, room_state, room_view
from match_service.domain.events import ANONYMOUS
from match_service.domain.model import MatchFinish, Phase, Presence
from match_service.infrastructure.rooms.codec import dumps, encode, loads

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_testkit.match import FakeLinkPort

pytestmark = pytest.mark.unit


def _code(reply: dict[str, Any]) -> str:
    return str(reply["code"])


class LevelOutage(FakeLinkPort):
    """Ссылки уровня ``level`` недоступны первые ``times`` запросов (503 media-pipeline)."""

    def __init__(self, level: int, times: int) -> None:
        super().__init__()
        self.level = level
        self.left = times
        self.failed = 0

    async def link(self, asset: Any, level: int, subject: str) -> Any:
        if level == self.level and self.left > 0:
            self.left -= 1
            self.failed += 1
            raise DependencyUnavailableError("media-pipeline недоступен")
        return await super().link(asset, level, subject)


class BrokenLinks(FakeLinkPort):
    """Ссылки падают не ``AppError``, а произвольным исключением (баг клиента)."""

    async def link(self, asset: Any, level: int, subject: str) -> Any:
        raise RuntimeError("неожиданный ответ")


# ----------------------------------------------------------------------------- A1
async def test_a1_seed_is_secret_and_differs_between_starts_with_same_match_id() -> None:
    match_id = uuid7()
    seeds = []
    for _ in range(2):
        h = Harness(fixed_id=match_id)
        room, host = await h.room()
        (b,) = await h.join(room, 1)
        await h.start(room, host)
        match = room.match
        assert match is not None and match.match_id == match_id
        seeds.append(match.seed)
        now = h.now_ms()
        visible = json.dumps(
            [
                room_view(room, now),
                match_view(room),
                room_state(room, b, now),
                messages(room, room.pull_changes(), now),
            ],
            default=str,
        )
        assert match.seed not in visible
        assert str(match_id) not in match.seed  # не выводится из match_id
    assert seeds[0] != seeds[1]


# ----------------------------------------------------------------------------- A2 / B2
async def test_a2_poison_command_does_not_hang_room_and_series_aborts() -> None:
    h = Harness(poison_limit=3)
    room, host = await h.room()
    (b,) = await h.join(room, 1)

    async def crash(room: Any, cmd: RoomCommand, now: int) -> Any:
        raise RuntimeError("баг обработчика")

    h.owner._handlers["transfer_host"] = crash
    reply = await h.cmd(room, host, "transfer_host", user_id=str(b))
    assert reply["status"] == 503 and _code(reply) == "match.unavailable"
    # Комната продолжает принимать команды, успех обнуляет серию.
    await h.ok(room, b, "ready", ready=False)
    assert room.phase is Phase.LOBBY
    for _ in range(3):
        await h.cmd(room, host, "transfer_host", user_id=str(b))
    assert room.phase is Phase.CLOSED and room.close_reason == "internal_error"


async def test_a2_resolve_crash_voids_round_instead_of_crashing_tick() -> None:
    h = Harness(assets=BrokenLinks())
    room, host = await h.room()
    await h.join(room, 1)
    await h.start(room, host)
    assert room.phase is Phase.REVEAL
    assert room.match is not None and room.match.records[0].void is True


# ----------------------------------------------------------------------------- A3 / R8
async def test_a3_op_window_is_compact_without_room_views() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    await h.join(room, 1)
    await h.ok(room, host, "settings", settings={"rounds": 12})
    ops = encode(room)["ops"]
    assert ops and all(
        set(outcome) <= {"status", "code", "details", "result"}
        for per_user in ops.values()
        for outcome in per_user.values()
    )


# ----------------------------------------------------------------------------- A12
async def test_a12_op_window_is_per_player() -> None:
    h = Harness(op_window=4)
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    (b,) = await h.join(room, 1)
    first = await h.cmd(room, host, "chat", op_id="op-host-chat", text="раз")
    for i in range(10):  # чужие команды не вытесняют мою из окна
        await h.ok(room, b, "chat", text=f"b{i}")
    again = await h.cmd(room, host, "chat", op_id="op-host-chat", text="раз")
    assert again["status"] == first["status"] == 200
    chats = [c for c in room.changes if c.kind == "chat" and c.data["user_id"] == str(host)]
    assert len(chats) == 1


# ----------------------------------------------------------------------------- A5
async def test_a5_removed_player_nickname_is_blanked() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    await h.ok(room, b, "remove")  # команда consumer'а UserDeleted от имени удалённого
    seat = room.seat_of(b)
    assert seat is None or (seat.nickname == "" and seat.presence is Presence.LEFT)
    del host, c


async def test_a5_user_deleted_mid_match_is_anonymous_in_result() -> None:
    h = Harness()
    room, host = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, host)
    await h.ok(room, b, "remove")
    assert room.phase is Phase.SUMMARY and b in room.forgotten
    (event,) = h.records.finished.values()
    ids = {p["user_id"] for p in event.players}
    assert b not in ids and ANONYMOUS in ids and host in ids
    assert b not in {r["user_id"] for rnd in event.rounds for r in rnd["results"]}
    assert loads(dumps(room)).forgotten == {b}


# ----------------------------------------------------------------------------- A7
async def test_a7_free_chat_only_outside_levels() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    (b,) = await h.join(room, 1)
    await h.ok(room, b, "chat", text="в лобби можно")
    await h.start(room, host)
    assert room.phase is Phase.LEVEL_OPEN
    denied = await h.cmd(room, b, "chat", text="ответ — Титаник")
    assert denied["status"] == 422 and _code(denied) == "match.chat_disabled"
    assert denied["details"] == {"reason": "phase"}


# ----------------------------------------------------------------------------- A9
async def test_a9_kick_leave_remove_revoke_channel_subscription() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c, d = await h.join(room, 3)
    await h.ok(room, host, "kick", user_id=str(b))
    await h.ok(room, c, "leave")
    channel = f"room:{room.code}"
    assert h.subscriptions.revoked == [(b, channel), (c, channel)]
    # Centrifugo недоступен: команда всё равно проходит.
    h.subscriptions.fail = True
    await h.ok(room, d, "remove")
    assert all(s.user_id != d for s in room.seats)


# ----------------------------------------------------------------------------- B1
async def test_b1_snapshot_keeps_speed_timing_for_scoring() -> None:
    h = Harness()
    room, host = await h.room("film.party.frame")
    b, c = await h.join(room, 2)
    await h.start(room, host)
    h.clock.advance(seconds=4)
    await h.ok(room, host, "guess", **h.guess_data(room, h.title(room)))
    room.pull_changes()
    restored = loads(dumps(room))
    h2 = Harness(clock=h.clock, bodies=h.bodies)
    for owner, current in ((h, room), (h2, restored)):
        for user in (b, c):
            await owner.ok(
                current, user, "guess", **owner.guess_data(current, owner.title(current))
            )
    assert room.match is not None and restored.match is not None
    score = room.match.players[host].score
    assert 0 < score < 100  # очки за скорость: 4 с из окна
    assert restored.match.players[host].score == score


# ----------------------------------------------------------------------------- B3
async def test_b3_closed_room_keeps_result_until_written() -> None:
    h = Harness()
    room, host = await h.room()
    await h.join(room, 1)
    await h.start(room, host)
    h.records.fail = True
    await h.ok(room, uuid7(), "abort")
    assert room.phase is Phase.CLOSED
    assert h.owner.unpersisted(room) and not h.records.finished
    h.records.fail = False
    await h.tick(room, 1)
    assert not h.owner.unpersisted(room)
    (event,) = h.records.finished.values()
    assert event.aborted is True


# ----------------------------------------------------------------------------- B4
async def test_b4_rejoin_by_link_resumes_paused_duel() -> None:
    h = Harness()
    room, host = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, host)
    h.offline.add(b)
    await h.tick(room, 13)
    assert room.phase is Phase.PAUSED
    await h.ok(room, b, "join", nickname="b")
    assert room.phase is Phase.LEVEL_OPEN
    assert room.match is not None and room.match.players[b].disconnected_at_ms is None


async def test_b4_start_requires_every_seat_online() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    (b,) = await h.join(room, 1)
    h.offline.add(b)
    await h.tick(room, 13)
    seat = room.seat_of(b)
    assert seat is not None and seat.presence is Presence.DISCONNECTED and seat.ready
    reply = await h.cmd(room, host, "start", expected_version=room.version)
    assert reply["status"] == 409 and _code(reply) == "match.not_all_ready"
    assert reply["details"] == {"reason": "disconnected"}


# ----------------------------------------------------------------------------- R9
async def test_r9_media_outage_is_retried_once() -> None:
    links = LevelOutage(level=1, times=1)
    h = Harness(assets=links)
    room, host = await h.room()
    await h.join(room, 1)
    await h.start(room, host)
    assert links.failed == 1 and room.phase is Phase.LEVEL_OPEN


async def test_r9_lost_level_keeps_points_of_earlier_guessers() -> None:
    links = LevelOutage(level=2, times=2)  # оба запроса уровня 2 неудачны
    h = Harness(assets=links)
    room, host = await h.room("film.party.frame")
    b, c = await h.join(room, 2)
    await h.start(room, host)
    await h.ok(room, host, "guess", **h.guess_data(room, h.title(room)))
    for user in (b, c):
        await h.ok(room, user, "skip", **h.turn(room))
    match = room.match
    assert match is not None and links.failed == 2
    assert room.phase in (Phase.REVEAL, Phase.LEVEL_OPEN)
    record = match.records[0]
    assert record.void is False
    assert match.players[host].score > 0
    assert match.players[b].score == match.players[c].score == 0


# ----------------------------------------------------------------------------- R10
async def test_r10_rematch_forgets_previous_match_rules() -> None:
    h = Harness()
    room, host = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, host)
    match = room.match
    assert match is not None
    assert match.match_id in h.owner._rules
    await h.ok(room, b, "forfeit")
    await h.ok(room, host, "rematch")
    assert room.rematch_code is not None
    assert match.match_id not in h.owner._rules


# ----------------------------------------------------------------------------- MatchFinished
async def test_abort_during_countdown_writes_no_result() -> None:
    h = Harness()
    room, host = await h.room()
    await h.join(room, 1)
    await h.ok(room, host, "start", expected_version=room.version)
    assert room.phase is Phase.COUNTDOWN
    await h.ok(room, uuid7(), "abort")
    match = room.match
    assert match is not None and match.finish is MatchFinish.ABORTED
    assert room.phase is Phase.CLOSED and not h.owner.unpersisted(room)
    assert h.records.started == {} and h.records.finished == {}
