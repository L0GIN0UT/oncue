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
from match_service.domain import match_fsm
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


# ----------------------------------------------------------------------------- A2 / B2 / Н1 / Н2
async def test_n1_crash_after_mutation_rolls_back_and_poison_is_skipped() -> None:
    h = Harness(poison_limit=3)
    room, host = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, host)
    original = h.owner._handlers["guess"]

    async def guess_then_crash(room: Any, cmd: RoomCommand, now: int) -> Any:
        await original(room, cmd, now)  # попытка уже засчитана в комнате…
        raise RuntimeError("баг после мутации")  # …и тут обработчик падает

    h.owner._handlers["guess"] = guess_then_crash
    data = h.guess_data(room, "мимо")
    version = room.version
    # Runner перечитывает снимок и повторяет запись: две попытки — проброс, третья — 503.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await h.cmd(room, b, "guess", op_id="op-poison-1", **data)
        await h.ok(room, host, "away", away=False)  # прежние команды пакета не сбрасывают серию
    reply = await h.cmd(room, b, "guess", op_id="op-poison-1", **data)
    assert reply["status"] == 503 and _code(reply) == "match.unavailable"
    match = room.match
    assert match is not None and match.contest is not None
    assert match.contest.players[str(b)].state.attempts_on_level == 0  # откат
    assert room.op_reply(b, "op-poison-1") is None and room.version >= version
    assert room.phase is Phase.LEVEL_OPEN  # комната цела, команда пропущена
    # Тот же op_id после починки применяется ровно один раз.
    h.owner._handlers["guess"] = original
    first = await h.cmd(room, b, "guess", op_id="op-poison-1", **data)
    again = await h.cmd(room, b, "guess", op_id="op-poison-1", **data)
    assert first == again
    assert match.contest is not None
    assert match.contest.players[str(b)].state.attempts_on_level == 1


async def test_n2_infrastructure_failures_are_retried_not_counted() -> None:
    h = Harness(poison_limit=2)
    room, host = await h.room()
    (b,) = await h.join(room, 1)

    async def redis_down(room: Any, cmd: RoomCommand, now: int) -> Any:
        raise ConnectionError("redis недоступен")

    h.owner._handlers["transfer_host"] = redis_down
    for _ in range(5):
        with pytest.raises(ConnectionError):
            await h.cmd(room, host, "transfer_host", op_id="op-infra-1", user_id=str(b))
    assert not room.is_closed and h.owner._failures == {}


async def test_n2_tick_series_resets_on_success_and_forget_clears_counters() -> None:
    h = Harness(poison_limit=3)
    room, host = await h.room()
    await h.join(room, 1)
    await h.start(room, host)
    calls = {"fail": True}
    original = match_fsm.presence

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if calls["fail"]:
            raise RuntimeError("баг тика")
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(match_fsm, "presence", flaky)
        for _ in range(2):
            await h.tick(room, 0)
        calls["fail"] = False
        await h.tick(room, 0)  # успех обнуляет серию
        calls["fail"] = True
        for _ in range(2):
            await h.tick(room, 0)
        assert not room.is_closed
        h.owner.forget(room)
        assert room.code not in h.owner._tick_failures
        for _ in range(3):
            await h.tick(room, 0)
    assert room.is_closed and room.close_reason == "internal_error"


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


# ----------------------------------------------------------------------------- Н8
async def test_n8_op_window_only_for_members_and_bounded() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    (b,) = await h.join(room, 1)
    for _ in range(5):  # посторонние не попадают в окно
        await h.cmd(room, uuid7(), "chat", text="спам")
    assert str(b) in room.ops and set(room.ops) <= {str(host), str(b)}
    await h.cmd(room, host, "kick", user_id=str(b))
    assert str(b) not in room.ops  # кик чистит окно

    capped = Harness(op_users_max=2)
    lobby, owner = await capped.room("film.lobby.collection", collection_ids=(uuid7(),))
    users = await capped.join(lobby, 3)
    assert len(lobby.ops) == 2 and str(users[-1]) in lobby.ops
    del owner


async def test_n8_repeated_leave_returns_same_reply() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    first = await h.cmd(room, b, "leave", op_id="op-leave-1")
    assert first["status"] == 200
    again = await h.cmd(room, b, "leave", op_id="op-leave-1")
    assert again == first
    assert room.op_reply(b, "op-leave-1") == {"status": 200}
    del host, c


# ----------------------------------------------------------------------------- Н7
async def test_n7_one_active_room_join_join_and_pending_pointer() -> None:
    h = Harness()
    first, _ = await h.room("film.lobby.collection", code="AAAAAAAA", collection_ids=(uuid7(),))
    second, _ = await h.room("film.lobby.collection", code="BBBBBBBB", collection_ids=(uuid7(),))
    p = uuid7()
    # Вход в первую ещё идёт (указатель есть, места нет) — во вторую нельзя.
    assert await h.player_rooms.claim(p, first.code, h.now_ms())
    busy = await h.cmd(second, p, "join", nickname="p")
    assert busy["status"] == 409 and _code(busy) == "match.active_room_conflict"
    await h.ok(first, p, "join", nickname="p")
    again = await h.cmd(second, p, "join", nickname="p")
    assert _code(again) == "match.active_room_conflict"

    # Свежий указатель на ещё не созданную комнату — занято; устаревший — заменяется.
    q = uuid7()
    assert await h.player_rooms.claim(q, "ZZZZZZZZ", h.now_ms())
    pending = await h.cmd(second, q, "join", nickname="q")
    assert _code(pending) == "match.active_room_conflict"
    h.clock.advance(seconds=11)
    await h.ok(second, q, "join", nickname="q")
    assert await h.player_rooms.get(q) == second.code


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


async def test_kick_disconnects_target_with_reconnect_and_survives_failure() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    await h.ok(room, host, "kick", user_id=str(b))
    assert h.subscriptions.disconnects == [(b, True, "kicked")]
    h.subscriptions.fail_disconnect = True  # сбой disconnect не роняет владельца
    await h.ok(room, host, "kick", user_id=str(c))
    assert all(s.user_id != c for s in room.seats)


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
    await h.ok(room, b, "rematch")
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


# ----------------------------------------------------------------------------- e2e CI: «матч встал»
async def test_parallel_next_level_reopens_own_move_only_in_state() -> None:
    """После пропуска свой ход закрыт до закрытия уровня; на следующем уровне того же раунда
    ``me.attempts_left`` снова > 0 — но ``me`` приходит только в ``GET /state`` (ADR 0035),
    дельта ``level_opened`` его не несёт: клиент обязан перечитать снимок на новом уровне."""
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    await h.start(room, host)
    await h.ok(room, host, "skip", **h.turn(room))
    mine = room_state(room, host, h.now_ms())["me"]
    assert mine["attempts_left"] == 0 and mine["status"] == "answered"
    again = await h.cmd(room, host, "skip", **h.turn(room))
    assert again["status"] == 409 and _code(again) == "match.level_locked"
    assert room.match is not None and room.match.statuses[str(host)] == "answered"
    for user in (b, c):
        await h.ok(room, user, "skip", **h.turn(room))
    match = room.match
    assert match.contest is not None and match.contest.level == 2
    state = room_state(room, host, h.now_ms())
    assert state["me"]["attempts_left"] == 1 and state["me"]["status"] == "thinking"
    opened = messages(room, room.pull_changes(), h.now_ms())
    assert opened and all("me" not in m["delta"] for m in opened)
