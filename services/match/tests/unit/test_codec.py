"""Формат снимка комнаты (§13 плана): снимок прошлой версии читается после деплоя.

``data/room_snapshot.v1.json`` — сохранённый снимок идущего матча (party, раунд 0, уровень
1, ходы игроков). При ``SCHEMA_VERSION + 1`` файл не меняется: тест обязан читать его через
шаги ``_UPGRADES``, а для новой версии добавляется свой файл.
"""

from __future__ import annotations

import json
from dataclasses import replace
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
        same = set(data) - {"ops", "match", "seats", "settings", "mode"}
        assert {k: encoded[k] for k in same} == {k: data[k] for k in same}
        # preload_next_round режима (ADR 0080) - новое поле с умолчанием False.
        assert encoded["mode"].pop("preload_next_round") is False
        assert encoded["mode"] == data["mode"]
        assert encoded["match"]["next_round_first_clue"] is None
        assert encoded["start_error"] is None
        # Название комнаты добавлено после снимка: читается как «не задано», остальное то же.
        assert encoded["settings"].pop("name") is None
        assert encoded["settings"] == data["settings"]
        # listen_s места, artwork матча и artwork_asset_set_id раунда (ADR 0073) - новые поля
        # со значением по умолчанию: старый снимок читается без шага _UPGRADES.
        # clip_sync места и clip_gate матча (общий старт таймера) - тоже новые поля с умолчанием.
        new_seat_keys = {"listen_s", "clip_sync"}
        assert [
            {k: v for k, v in s.items() if k not in new_seat_keys} for s in encoded["seats"]
        ] == data["seats"]
        assert all(s["listen_s"] is None and s["clip_sync"] is False for s in encoded["seats"])
        assert encoded["match"]["clip_gate"] is None
        assert encoded["match"]["artwork"] is None
        assert all(r["artwork_asset_set_id"] is None for r in encoded["match"]["plan"])
        encoded = encode(room)
        assert encode(decode(encoded)) == encoded
        legacy = {tuple(key.split(":", 1)) for key in data["ops"]}
        compact = {(user, op) for user, ops in encoded["ops"].items() for op in ops}
        assert compact == legacy
        for ops in encoded["ops"].values():
            for outcome in ops.values():
                assert "body" not in outcome or "room" not in outcome["body"]
        players = encoded["match"]["contest"]["players"]
        assert all(p["elapsed_ms"] is None and p["window_ms"] is None for p in players.values())


async def test_room_name_survives_snapshot_without_schema_bump() -> None:
    h = Harness()
    room, _ = await h.room()
    assert room.settings.name is None
    named = replace(room, settings=replace(room.settings, name="Вечер кино"))
    data = encode(named)
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["settings"]["name"] == "Вечер кино"
    assert loads(dumps(named)).settings.name == "Вечер кино"


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


async def test_artwork_and_listen_survive_snapshot_round_trip() -> None:
    h = Harness(artwork_sets=True)
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    await h.ok(room, a, "listen", level=1, position_ms=2500)
    assert room.match is not None
    art_set = room.match.plan[0].artwork_asset_set_id
    assert art_set is not None

    restored = loads(dumps(room))

    assert restored.seat_of(a).listen_s == 2 and restored.seat_of(b).listen_s is None  # type: ignore[union-attr]
    assert restored.match is not None
    assert restored.match.plan[0].artwork_asset_set_id == art_set
    assert encode(restored) == encode(room)


async def test_clip_gate_and_capability_survive_snapshot_without_schema_bump() -> None:
    """Затвор общего старта таймера переживает смену владельца; старый снимок читается без него."""
    h = Harness(clip_ready_max_wait_ms=10_000)
    room, host = await h.room()
    (guest,) = await h.join(room, 1)
    await h.ok(room, host, "clip_ready")
    await h.ok(room, guest, "clip_ready")
    await h.start(room, host)
    assert room.match is not None and room.match.clip_gate is not None
    back = loads(dumps(room))
    assert back.seat_of(host).clip_sync is True
    assert back.match is not None and back.match.clip_gate == room.match.clip_gate
    old = encode(room)
    del old["match"]["clip_gate"]
    assert decode(old).match.clip_gate is None  # снимок до затвора
    assert SCHEMA_VERSION == 1
