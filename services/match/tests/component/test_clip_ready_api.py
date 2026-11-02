"""Общий старт таймера уровня через HTTP API и канал (пункт 24 замечаний тестирования)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from match_app import MatchApp, Player

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

DUEL = "music.duel.classic"


def _channel(svc: MatchApp, code: str) -> list[dict[str, Any]]:
    return [dict(m) for m in svc.realtime.room(code)]


async def _announce(svc: MatchApp, code: str, *players: Player) -> None:
    for player in players:
        await svc.ok(player, code, "clip-ready")


async def _confirm(svc: MatchApp, code: str, player: Player, state: dict[str, Any]) -> None:
    match = state["match"]
    await svc.ok(player, code, "clip-ready", round_index=match["round_index"], level=match["level"])


async def _start(svc: MatchApp, host: Player, code: str) -> dict[str, Any]:
    room = (await svc.state(host, code))["room"]
    await svc.ok(host, code, "start", expected_version=room["version"])
    svc.clock.advance(seconds=3)
    return await svc.until(
        host, code, lambda s: s["match"] is not None and s["match"]["level"] == 1
    )


async def test_timer_starts_by_common_signal_after_all_confirm(app_factory: Any) -> None:
    svc: MatchApp = await app_factory(clip_ready_max_wait_s=10)
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, DUEL))["code"]
    await svc.join(code, b)
    await _announce(svc, code, a, b)
    state = await _start(svc, a, code)
    assert state["match"]["awaiting_clips"] is True and state["match"]["clip_wait_ends_at"]
    opened = [m for m in _channel(svc, code) if m["kind"] == "level_opened"][-1]
    assert opened["delta"]["awaiting_clips"] is True
    svc.clock.advance(seconds=4)  # фрагмент грузится - таймер стоит
    await svc.heartbeat(code, [a, b])
    assert (await svc.state(a, code))["match"]["awaiting_clips"] is True
    await _confirm(svc, code, a, state)
    assert (await svc.state(a, code))["match"]["awaiting_clips"] is True  # b ещё грузит
    await _confirm(svc, code, b, state)
    after = await svc.state(a, code)
    assert after["match"]["awaiting_clips"] is False
    started = [m for m in _channel(svc, code) if m["kind"] == "level_started"]
    assert len(started) == 1 and started[0]["delta"]["level"] == 1
    assert datetime.fromisoformat(after["room"]["phase_ends_at"]) == datetime.fromisoformat(
        started[0]["delta"]["phase_ends_at"]
    )
    svc.realtime.assert_versions_contiguous(f"room:{code}")


async def test_without_confirmations_everything_works_as_before(app_factory: Any) -> None:
    svc: MatchApp = await app_factory(clip_ready_max_wait_s=10)
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, DUEL))["code"]
    await svc.join(code, b)
    state = await _start(svc, a, code)
    assert state["match"]["awaiting_clips"] is False
    kinds = {m["kind"] for m in _channel(svc, code)}
    assert "level_started" not in kinds
    opened = [m for m in _channel(svc, code) if m["kind"] == "level_opened"][-1]
    assert "awaiting_clips" not in opened["delta"]


async def test_guess_while_clips_load_is_refused(app_factory: Any) -> None:
    svc: MatchApp = await app_factory(clip_ready_max_wait_s=10)
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, DUEL))["code"]
    await svc.join(code, b)
    await _announce(svc, code, a, b)
    state = await _start(svc, a, code)
    body = {"round_index": 0, "level": 1, "expected_version": state["room"]["version"]}
    refused = await svc.post(a, code, "match/guess", answer={"text": "что-то"}, **body)
    problem = assert_problem(refused, 409, "match.phase_mismatch")
    assert problem["details"] == {"awaiting_clips": True}


async def test_clip_ready_needs_round_and_level_together(svc: MatchApp) -> None:
    a = svc.user()
    code = (await svc.create(a, DUEL))["code"]
    bad = await svc.post(a, code, "clip-ready", level=1)
    assert bad.status_code == 422
