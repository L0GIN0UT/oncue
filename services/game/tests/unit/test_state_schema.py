"""Версия схемы ``game_sessions.state`` (план этапа 3a §12): снимки этапа 2 читаются как v1.

Снимок v1 ниже — ровно то, что писал ``_rounds_json`` этапа 2 (без ``state_schema``, плана
и состава раунда). Сессии, начатые до выкладки 3a, доигрываются без миграции данных.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from game_service.domain.session import (
    STATE_SCHEMA,
    GameSession,
    PlannedRound,
    PlayerKind,
    PlayerRef,
    RoundOption,
    SessionStatus,
)
from game_service.infrastructure.db.repositories import session_state_json, session_state_of

from gv_game_kernel import RoundResult

pytestmark = pytest.mark.unit

STARTED = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)

STAGE2_STATE: dict[str, Any] = {
    "rounds": [
        {
            "index": 0,
            "media_item_id": "0192a3b4-c5d6-7e8f-9a0b-000000000100",
            "asset": {"asset_set_id": "0192a3b4-c5d6-7e8f-9a0b-000000000300", "levels_count": 6},
            "levels_count": 6,
            "max_points": 6,
            "points": 4,
            "started_at": "2026-10-10T09:00:00+00:00",
            "finished_at": "2026-10-10T09:00:12+00:00",
            "state": {
                "level": 3,
                "attempts_on_level": 1,
                "outcome": "exact",
                "partial_awarded_level": None,
                "guesses": [
                    {"kind": "text", "level": 1, "verdict": "miss", "display": "не то"},
                    {"kind": "skip", "level": 2, "verdict": None, "display": ""},
                    {"kind": "item", "level": 3, "verdict": "exact", "display": "Трек"},
                ],
            },
        },
        {
            "index": 1,
            "media_item_id": "0192a3b4-c5d6-7e8f-9a0b-000000000101",
            "asset": None,
            "levels_count": 6,
            "max_points": 6,
            "points": 0,
            "started_at": None,
            "finished_at": "2026-10-10T09:00:00+00:00",
            "state": {
                "level": 1,
                "attempts_on_level": 0,
                "outcome": "void",
                "partial_awarded_level": None,
                "guesses": [],
            },
        },
        {
            "index": 2,
            "media_item_id": "0192a3b4-c5d6-7e8f-9a0b-000000000102",
            "asset": {"asset_set_id": "0192a3b4-c5d6-7e8f-9a0b-000000000302", "levels_count": 6},
            "levels_count": 6,
            "max_points": 6,
            "points": 0,
            "started_at": None,
            "finished_at": None,
            "state": {
                "level": 1,
                "attempts_on_level": 0,
                "outcome": None,
                "partial_awarded_level": None,
                "guesses": [],
            },
        },
    ]
}


def test_stage2_snapshot_reads_as_v1() -> None:
    state = session_state_of(STAGE2_STATE)
    assert state.plan == [] and state.progressive is False and state.round_max is None
    first, void, current = state.rounds
    assert first.state.outcome is RoundResult.EXACT and first.points == 4
    assert first.elapsed_ms == 12000 and first.shown == () and first.options == ()
    assert void.is_void and void.asset is None
    assert current.choice is None and current.stage is None and current.reveal_asset is None


def test_v1_session_keeps_playing_and_round_max_from_rounds() -> None:
    state = session_state_of(STAGE2_STATE)
    session = GameSession(
        UUID(int=1),
        player=PlayerRef(PlayerKind.USER, UUID(int=2)),
        mode_key="music.daily.classic",
        mode_version=1,
        domain="music",
        source_type="daily",
        clue_type="audio_intro",
        day=None,
        preset_id=None,
        seed=b"s",
        status=SessionStatus.ACTIVE,
        rounds=state.rounds,
        started_at=STARTED,
        last_move_at=STARTED,
        idle_timeout_s=60,
        plan=state.plan,
        progressive=state.progressive,
        round_max=state.round_max,
    )
    assert session.round_max == 6 and session.current_index == 2
    assert session.max_score == 12  # void не входит, плана нет — как на этапе 2
    rewritten = session_state_json(session)
    assert rewritten["state_schema"] == STATE_SCHEMA and rewritten["plan"] == []


def test_v2_roundtrip_keeps_plan_options_and_choice() -> None:
    state = session_state_of(STAGE2_STATE)
    state.rounds[2].options = (RoundOption("a", media_item_id=UUID(int=7)), RoundOption("b"))
    state.rounds[2].choice = "a"
    state.rounds[2].stage = 32
    plan = [PlannedRound(UUID(int=9), shown=(UUID(int=8),), options=(RoundOption("higher"),))]
    raw = {
        "state_schema": STATE_SCHEMA,
        "rounds": STAGE2_STATE["rounds"],
        "plan": [],
        "progressive": True,
        "round_max": 1,
    }
    parsed = session_state_of(raw)
    assert parsed.progressive and parsed.round_max == 1
    session = GameSession(
        UUID(int=1),
        player=PlayerRef(PlayerKind.GUEST, UUID(int=2)),
        mode_key="film.bracket.weekly",
        mode_version=1,
        domain="film",
        source_type="bracket",
        clue_type="frame_progressive",
        day=None,
        preset_id=None,
        seed=b"s",
        status=SessionStatus.ACTIVE,
        rounds=state.rounds,
        started_at=STARTED,
        last_move_at=STARTED,
        idle_timeout_s=60,
        plan=plan,
        progressive=True,
        round_max=1,
    )
    again = session_state_of(session_state_json(session))
    assert again.plan == plan
    assert again.rounds[2].options == state.rounds[2].options
    assert (again.rounds[2].choice, again.rounds[2].stage) == ("a", 32)


def test_unknown_newer_schema_is_rejected() -> None:
    with pytest.raises(ValueError, match="версия схемы"):
        session_state_of({"state_schema": STATE_SCHEMA + 1, "rounds": []})
