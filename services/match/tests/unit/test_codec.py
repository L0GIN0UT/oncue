"""Формат снимка комнаты (§13 плана): снимок прошлой версии читается после деплоя.

``data/room_snapshot.v1.json`` — сохранённый снимок идущего матча (party, раунд 0, уровень
1, ходы игроков). При ``SCHEMA_VERSION + 1`` файл не меняется: тест обязан читать его через
шаги ``_UPGRADES``, а для новой версии добавляется свой файл.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from match_harness import Harness
from match_service.domain.model import SCHEMA_VERSION, Phase
from match_service.infrastructure.rooms.codec import SnapshotError, decode, dumps, encode, loads

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "data" / "room_snapshot.v1.json"


def test_saved_v1_snapshot_is_readable_and_stable() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    room = decode(data)
    assert room.phase is Phase.LEVEL_OPEN
    assert room.match is not None and room.match.contest is not None
    guessed = [p for p in room.match.contest.players.values() if p.state.guesses]
    assert len(guessed) == 2
    if SCHEMA_VERSION == 1:
        # Поля, добавленные после сохранения снимка (start_error, elapsed_ms/window_ms
        # ходов), читаются по умолчанию; старое окно ops («user:op» → полный ответ)
        # сжимается до статуса и кода по игрокам; остальное не меняется при перезаписи.
        encoded = encode(room)
        same = set(data) - {"ops", "match"}
        assert {k: encoded[k] for k in same} == {k: data[k] for k in same}
        assert encoded["start_error"] is None
        assert encode(decode(encoded)) == encoded
        legacy = {tuple(key.split(":", 1)) for key in data["ops"]}
        compact = {(user, op) for user, ops in encoded["ops"].items() for op in ops}
        assert compact == legacy
        for ops in encoded["ops"].values():
            for outcome in ops.values():
                assert "body" not in outcome or "room" not in outcome["body"]
        players = encoded["match"]["contest"]["players"]
        assert all(p["elapsed_ms"] is None and p["window_ms"] is None for p in players.values())


async def test_live_room_round_trips_through_snapshot() -> None:
    h = Harness()
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    await h.ok(room, a, "guess", **h.guess_data(room, h.title(room)))
    room.pull_changes()
    again = loads(dumps(room))
    assert encode(again) == encode(room)
    # Восстановленная комната продолжает играть у нового владельца.
    h2 = Harness(clock=h.clock, bodies=h.bodies)
    await h2.ok(again, b, "guess", **h2.guess_data(again, h2.title(again)))
    await h2.tick(again, 5)
    assert again.match is not None and again.match.round_index == 1
    assert again.match.players[a].score == 12 and again.match.players[b].score == 6


def test_future_or_broken_snapshot_is_rejected() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(SnapshotError):
        decode({**data, "schema_version": SCHEMA_VERSION + 1})
    with pytest.raises(SnapshotError):
        decode({**data, "seats": None})
    with pytest.raises(SnapshotError):
        loads(b"not json")
