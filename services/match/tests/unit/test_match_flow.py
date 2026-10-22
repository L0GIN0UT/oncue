"""Автомат матча и команды владельца (R1–R3) на двойниках ядра и портов, ``FakeClock``."""

from __future__ import annotations

from typing import Any

import pytest
from match_harness import IDLE_MS, Harness
from match_service.domain.model import MatchFinish, Phase, PlayerFinish, Presence

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit


def _code(reply: dict[str, Any]) -> str:
    return str(reply["code"])


# ----------------------------------------------------------------------------- лобби
async def test_lobby_settings_reset_ready_and_only_host_changes_them() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    assert all(s.ready for s in room.seats if s.user_id != host)

    denied = await h.cmd(room, b, "settings", settings={"rounds": 12})
    assert denied["status"] == 403 and _code(denied) == "match.not_host"
    bad = await h.cmd(room, host, "settings", settings={"rounds": 7})
    assert _code(bad) == "match.setting_not_allowed" and bad["details"] == {"field": "rounds"}

    body = await h.ok(room, host, "settings", settings={"rounds": 12, "level_window_s": 25})
    assert body["room"]["settings"]["rounds"] == 12
    assert body["room"]["settings"]["level_window_s"] == 25
    assert not any(s.ready for s in room.seats)
    del c


async def test_kick_host_leave_and_rejoin_rules() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    assert _code(await h.cmd(room, host, "kick", user_id=str(host))) == "match.cannot_kick_self"
    assert _code(await h.cmd(room, b, "kick", user_id=str(c))) == "match.not_host"
    await h.ok(room, host, "kick", user_id=str(c))
    again = await h.cmd(room, c, "join", nickname="c")
    assert again["status"] == 403 and _code(again) == "match.kicked"
    assert await h.player_rooms.get(c) is None

    await h.ok(room, host, "leave")
    assert room.host_id == b
    assert await h.player_rooms.get(host) is None
    await h.ok(room, b, "leave")
    assert room.phase is Phase.CLOSED and room.close_reason == "empty"


async def test_join_conflicts_full_room_and_active_room() -> None:
    h = Harness()
    room, _ = await h.room()
    (b,) = await h.join(room, 1)
    full = await h.cmd(room, uuid7(), "join", nickname="x")
    assert full["status"] == 409 and _code(full) == "match.room_full"

    other, _ = await h.room(code="ABCDEFGH")
    busy = await h.cmd(other, b, "join", nickname="b")
    assert _code(busy) == "match.active_room_conflict" and busy["details"] == {"code": room.code}
    # Повторный вход участника — реконнект без конфликта.
    assert (await h.cmd(room, b, "join", nickname="b"))["status"] == 200


async def test_start_requires_ready_players_and_calls_plan_once() -> None:
    h = Harness()
    room, host = await h.room("film.party.frame")
    (b,) = await h.join(room, 1)
    few = await h.cmd(room, host, "start", expected_version=room.version)
    assert _code(few) == "match.not_enough_players"
    c = uuid7()
    await h.ok(room, c, "join", nickname="c")
    not_ready = await h.cmd(room, host, "start", expected_version=room.version)
    assert _code(not_ready) == "match.not_all_ready"
    await h.ok(room, c, "ready", ready=True)
    assert _code(await h.cmd(room, b, "start", expected_version=room.version)) == "match.not_host"

    await h.ok(room, host, "start", expected_version=room.version)
    assert room.phase is Phase.COUNTDOWN
    assert len(h.plans.requests) == 1 and h.plans.requests[0].rounds == 10
    await h.tick(room, 3)
    assert room.phase is Phase.LEVEL_OPEN
    assert room.match is not None and room.match.clue is not None
    assert len(h.records.started) == 1
    assert h.assets.subjects() == [f"room:{room.code}:0"]


async def test_plan_errors_keep_lobby_and_transient_error_is_not_remembered() -> None:
    h = Harness()
    room, host = await h.room()
    await h.join(room, 1)
    h.plans.pool_too_small(available=1)
    small = await h.cmd(room, host, "start", expected_version=room.version, op_id="op-start-1")
    assert small["status"] == 422 and _code(small) == "match.pool_too_small"
    assert room.phase is Phase.LOBBY

    h.plans.unavailable()
    down = await h.cmd(room, host, "start", expected_version=room.version, op_id="op-start-2")
    assert down["status"] == 503 and _code(down) == "match.game_unavailable"
    h.plans.heal()
    retried = await h.cmd(room, host, "start", expected_version=room.version, op_id="op-start-2")
    assert retried["status"] == 200 and room.phase is Phase.COUNTDOWN


# ----------------------------------------------------------------------------- дуэль
async def test_duel_exact_answer_closes_level_for_both() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    stale = h.guess_data(room, "мимо")
    win = await h.ok(room, a, "guess", **h.guess_data(room, h.title(room)))
    assert win["result"] == "exact" and win["level_closed"] is True
    # Опережение: уровень 1 закрыт для обоих, сопернику открыт уровень 2 со своим окном.
    match = room.match
    assert match is not None and match.contest is not None
    assert room.phase is Phase.LEVEL_OPEN and match.contest.level == 2
    late = await h.cmd(room, b, "guess", **stale)
    assert late["status"] == 409 and _code(late) == "match.phase_mismatch"

    second = await h.ok(room, b, "guess", **h.guess_data(room, h.title(room)))
    assert second["result"] == "exact" and room.phase is Phase.REVEAL
    assert match.players[a].score == 12 and match.players[b].score == 6


async def test_duel_attempts_and_window_open_next_level() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    for left in (2, 1, 0):
        body = await h.ok(room, b, "guess", **h.guess_data(room, "никак"))
        assert body["result"] == "miss" and body["attempts_left"] == left
    locked = await h.cmd(room, b, "guess", **h.guess_data(room, "ещё"))
    assert locked["status"] == 409 and _code(locked) == "match.level_locked"
    assert room.match is not None and room.match.contest is not None
    assert room.match.contest.level == 1 and room.match.statuses[str(b)] == "answered"

    await h.tick(room, 20)
    assert room.match.contest.level == 2 and room.phase is Phase.LEVEL_OPEN
    late = await h.ok(room, a, "guess", **h.guess_data(room, h.title(room)))
    assert late["result"] == "exact"
    # Опережение на последнем уровне завершает раунд.
    assert room.phase is Phase.REVEAL and room.match.players[a].score == 6


async def test_repeated_op_id_returns_saved_reply_without_reapplying() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    data = h.guess_data(room, "мимо")
    first = await h.cmd(room, b, "guess", op_id="op-guess-1", **data)
    again = await h.cmd(room, b, "guess", op_id="op-guess-1", **data)
    assert first == again
    assert room.match is not None and room.match.contest is not None
    assert room.match.contest.players[str(b)].state.attempts_on_level == 1


async def test_forfeit_ends_duel_and_result_is_written_once() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    await h.ok(room, b, "forfeit")
    assert room.phase is Phase.SUMMARY
    match = room.match
    assert match is not None and match.finish is MatchFinish.FORFEIT
    by_user = {s.user_id: s for s in match.standings}
    assert by_user[a].outcome == "win" and by_user[b].finish.value == "forfeit"
    assert len(h.records.finished) == 1 and match.finished_written
    await h.tick(room, 1)
    assert len(h.records.finished) == 1


async def test_duel_disconnect_pauses_and_reconnect_resumes_window() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    match = room.match
    assert match is not None and match.contest is not None
    ends_before = match.contest.level_ends_at_ms
    h.offline.add(b)
    await h.tick(room, 13)
    assert room.phase is Phase.PAUSED
    seat = room.seat_of(b)
    assert seat is not None and seat.presence is Presence.DISCONNECTED

    h.offline.clear()
    await h.tick(room, 5)
    assert room.phase is Phase.LEVEL_OPEN
    assert match.contest is not None and match.contest.level_ends_at_ms is not None
    assert ends_before is not None and match.contest.level_ends_at_ms > ends_before


async def test_duel_disconnect_beyond_grace_is_tech_win() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    h.offline.add(b)
    await h.tick(room, 13)
    await h.tick(room, 31)
    match = room.match
    assert match is not None and match.finish is MatchFinish.TECH_WIN
    finishes = {s.user_id: s.finish.value for s in match.standings}
    assert finishes == {a: "tech_win", b: "tech_loss"}


# ----------------------------------------------------------------------------- party
async def test_party_disconnected_player_drops_after_grace_with_score_kept() -> None:
    h = Harness()
    room, host = await h.room("film.party.frame")
    b, c, d = await h.join(room, 3)
    await h.start(room, host)
    for user in (c, host, b, d):
        await h.ok(room, user, "guess", **h.guess_data(room, h.title(room)))
    assert room.phase is Phase.REVEAL
    assert room.match is not None and room.match.players[c].score == 100
    await h.tick(room, 4)
    h.offline.add(c)
    await h.tick(room, 13)
    assert room.phase is not Phase.PAUSED
    await h.tick(room, 31)
    assert room.match.players[c].finish is not None
    assert room.match.players[c].score == 100
    seat = room.seat_of(c)
    assert seat is not None and seat.presence is Presence.LEFT
    assert room.phase in (Phase.LEVEL_OPEN, Phase.REVEAL)
    del b, d


async def test_party_all_answered_closes_level_early_and_skip_waits() -> None:
    h = Harness()
    room, host = await h.room("film.party.frame")
    b, c = await h.join(room, 2)
    await h.start(room, host)
    await h.ok(room, host, "guess", **h.guess_data(room, h.title(room)))
    await h.ok(
        room, b, "skip", **{k: v for k, v in h.guess_data(room, "").items() if k != "answer"}
    )
    assert room.phase is Phase.LEVEL_OPEN
    assert room.match is not None and room.match.statuses[str(b)] == "answered"
    await h.ok(room, c, "guess", **h.guess_data(room, h.title(room)))
    # Ответили все: уровень закрыт досрочно, пасовавшему открыт следующий.
    assert room.match.contest is not None and room.match.contest.level == 2
    assert room.match.statuses == {str(host): "guessed", str(b): "thinking", str(c): "guessed"}


async def test_party_full_match_to_summary_and_lobby_rematch_keeps_memory() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    await h.ok(room, host, "settings", settings={"rounds": 5})
    for user in (b, c):
        await h.ok(room, user, "ready", ready=True)
    await h.start(room, host)
    for _ in range(5):
        await h.ok(room, b, "guess", **h.guess_data(room, h.title(room)))
        await h.until(room, Phase.REVEAL)
        await h.until(room, Phase.LEVEL_OPEN, Phase.SUMMARY)
    assert room.phase is Phase.SUMMARY
    assert room.match is not None and room.match.standings[0].user_id == b
    assert len(room.memory) == 5
    for user in (host, b, c):
        body = await h.ok(room, user, "rematch")
    assert body["rematch_code"] == room.code and room.phase is Phase.LOBBY
    assert len(room.memory) == 5
    await h.ok(room, host, "reset_memory")
    assert room.memory == []


async def test_duel_rematch_creates_new_room_with_same_players() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    await h.ok(room, b, "forfeit")
    # Сдача — исход матча, не выход из комнаты: место B остаётся, реванш ждёт его голоса.
    seat = room.seat_of(b)
    assert seat is not None and seat.presence is not Presence.LEFT
    assert room.match is not None and room.match.players[b].finish is not None
    first = await h.ok(room, a, "rematch")
    assert first["rematch_code"] is None and len(h.store.rooms) == 1
    body = await h.ok(room, b, "rematch")
    code = body["rematch_code"]
    assert code is not None and code != room.code
    fresh = h.store.rooms[code]
    assert {s.user_id for s in fresh.seats} == {a, b} and fresh.host_id == a
    assert await h.player_rooms.get(a) == code
    assert await h.player_rooms.get(b) == code
    assert room.phase is Phase.CLOSED and room.close_reason == "rematch"
    closed = [c.data for c in room.changes if c.kind == "closed"]
    assert closed == [{"reason": "rematch", "rematch_code": code}]


async def test_duel_rematch_after_leave_needs_only_remaining_player() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    await h.ok(room, b, "leave")  # выход из комнаты — место LEFT
    body = await h.ok(room, a, "rematch")
    fresh = h.store.rooms[body["rematch_code"]]
    assert [s.user_id for s in fresh.seats] == [a]


async def test_party_forfeited_player_stays_and_votes_for_rematch() -> None:
    h = Harness()
    room, host = await h.room("film.party.frame")
    b, c = await h.join(room, 2)
    await h.start(room, host)
    await h.ok(room, c, "forfeit")
    match = room.match
    assert match is not None and not match.players[c].active
    assert room.phase in (Phase.LEVEL_OPEN, Phase.REVEAL)  # матч идёт без C
    seat = room.seat_of(c)
    assert seat is not None and seat.presence is Presence.ONLINE
    denied = await h.cmd(room, c, "guess", **h.guess_data(room, h.title(room)))
    assert denied["status"] == 403
    await h.ok(room, b, "forfeit")
    assert room.phase is Phase.SUMMARY
    for user in (host, b):
        assert (await h.ok(room, user, "rematch"))["rematch_code"] is None
    body = await h.ok(room, c, "rematch")
    fresh = h.store.rooms[body["rematch_code"]]
    assert {s.user_id for s in fresh.seats} == {host, b, c}


async def test_party_player_returns_before_grace_and_keeps_playing() -> None:
    h = Harness()
    room, host = await h.room("film.party.frame")
    b, c = await h.join(room, 2)
    await h.start(room, host)
    h.offline.add(c)
    await h.tick(room, 13)  # ≥ 12 с без tick — «отключён»
    seat = room.seat_of(c)
    assert seat is not None and seat.presence is Presence.DISCONNECTED
    await h.ok(room, c, "join", nickname="c")  # возврат по ссылке до grace (30 с)
    h.offline.clear()
    await h.tick(room, 1)
    match = room.match
    assert match is not None and match.players[c].active
    assert match.players[c].disconnected_at_ms is None
    assert seat.presence is Presence.ONLINE
    await h.until(room, Phase.LEVEL_OPEN)
    await h.ok(room, c, "guess", **h.guess_data(room, h.title(room)))
    await h.tick(room, 40)  # grace прошла бы — но C на связи и в матче
    assert match.players[c].finish is not PlayerFinish.LEFT  # не выбыл по grace
    assert seat.presence is not Presence.LEFT
    del b


async def test_unavailable_asset_voids_round() -> None:
    h = Harness()
    room, a = await h.room()
    await h.join(room, 1)
    await h.ok(room, a, "start", expected_version=room.version)
    match = room.match
    assert match is not None
    h.assets.gone(match.plan[0].levels[0].asset_set_id)
    await h.tick(room, 3)
    assert room.phase is Phase.REVEAL
    assert match.records[0].void is True


# ----------------------------------------------------------------------------- чат
async def test_emotes_and_chat_rules() -> None:
    h = Harness()
    room, a = await h.room()
    await h.join(room, 1)
    await h.ok(room, a, "emote", kind="emote", key="match.emotes.gg")
    unknown = await h.cmd(room, a, "emote", kind="phrase", key="match.emotes.gg")
    assert _code(unknown) == "match.emote_unknown"
    disabled = await h.cmd(room, a, "chat", text="привет")
    assert disabled["status"] == 403 and _code(disabled) == "match.chat_disabled"
    outsider = await h.cmd(room, uuid7(), "emote", kind="emote", key="match.emotes.gg")
    assert _code(outsider) == "match.not_participant"


async def test_idle_lobby_closes_and_persist_failure_is_retried() -> None:
    h = Harness()
    room, _ = await h.room()
    await h.tick(room, IDLE_MS / 1000 + 1)
    assert room.phase is Phase.CLOSED and room.close_reason == "idle"

    h2 = Harness()
    room2, a = await h2.room()
    await h2.join(room2, 1)
    h2.records.fail = True
    await h2.start(room2, a)
    assert room2.match is not None and not room2.match.started_written
    h2.records.fail = False
    await h2.tick(room2, 0)  # пауза перед повтором ещё не прошла
    assert not room2.match.started_written
    await h2.tick(room2, 1)
    assert room2.match.started_written and len(h2.records.started) == 1


async def test_admin_abort_closes_room_with_aborted_match() -> None:
    h = Harness()
    room, a = await h.room()
    await h.join(room, 1)
    await h.start(room, a)
    body = await h.ok(room, uuid7(), "abort")
    assert body["accepted"] is True and room.phase is Phase.CLOSED
    match = room.match
    assert match is not None and match.finish is MatchFinish.ABORTED
    (event,) = h.records.finished.values()
    assert event.aborted is True and event.abort_reason == "admin"
