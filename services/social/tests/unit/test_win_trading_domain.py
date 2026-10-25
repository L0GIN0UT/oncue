"""Правила сигналов win-trading: повторные пары и серии сдач (ADR 0068). Чистый домен, без IO."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from social_service.domain.config import ForfeitSeriesConfig, PairRepeatConfig, WinTradingConfig
from social_service.domain.win_trading import (
    MatchFact,
    evaluate_forfeit_series,
    evaluate_forfeit_window,
    evaluate_pair,
    fact_from_event,
    pair_of,
)

pytestmark = pytest.mark.unit

A = UUID(int=1)
B = UUID(int=2)
C = UUID(int=3)
T0 = datetime(2026, 10, 1, tzinfo=UTC)
CFG = WinTradingConfig(
    pair_repeat=PairRepeatConfig(window_s=3600, min_matches=3, strong_streak=3),
    forfeit_series=ForfeitSeriesConfig(window_s=3600, streak_min=2, window_min=3),
)


def fact(
    minutes: int,
    winner: UUID | None = None,
    forfeiter: UUID | None = None,
    pair: tuple[UUID, UUID] = (A, B),
) -> MatchFact:
    return MatchFact(
        match_id=uuid4(),
        user_low=pair[0],
        user_high=pair[1],
        winner_id=winner,
        forfeiter_id=forfeiter,
        finished_at=T0 + timedelta(minutes=minutes),
    )


def newest_first(*facts: MatchFact) -> list[MatchFact]:
    return sorted(facts, key=lambda f: f.finished_at, reverse=True)


def test_pair_below_threshold_is_not_a_signal() -> None:
    assert evaluate_pair(newest_first(fact(0, A), fact(1, B)), CFG.pair_repeat) is None


def test_pair_counts_both_directions_and_wins() -> None:
    signal = evaluate_pair(newest_first(fact(0, A), fact(1, B), fact(2, A)), CFG.pair_repeat)
    assert signal is not None
    assert (signal.kind, signal.user_id, signal.other_user_id) == ("pair_repeat", A, B)
    assert signal.count == 3
    assert signal.details["wins"] == {str(A): 2, str(B): 1}
    assert signal.streak == 1
    assert signal.severity == "medium"


def test_same_winner_streak_makes_signal_high() -> None:
    facts = newest_first(fact(0, B), fact(1, A), fact(2, A), fact(3, A))
    signal = evaluate_pair(facts, CFG.pair_repeat)
    assert signal is not None
    assert signal.streak == 3
    assert signal.severity == "high"


def test_matches_outside_window_are_ignored() -> None:
    # Три матча, но старейший старше окна от последнего: порог не достигнут.
    facts = newest_first(fact(0, A), fact(100, A), fact(101, A))
    assert evaluate_pair(facts, CFG.pair_repeat) is None


def test_draw_breaks_streak() -> None:
    signal = evaluate_pair(
        newest_first(fact(0, A), fact(1, None), fact(2, A), fact(3, A)), CFG.pair_repeat
    )
    assert signal is not None
    assert signal.streak == 2


def test_forfeit_streak_counts_only_consecutive_by_same_player() -> None:
    facts = newest_first(fact(0, B, forfeiter=A), fact(1, B, forfeiter=A), fact(-5, A))
    signal = evaluate_forfeit_series(facts, A, CFG.forfeit_series)
    assert signal is not None
    assert (signal.kind, signal.user_id, signal.other_user_id) == ("forfeit_series", A, B)
    assert signal.streak == 2
    assert signal.count == 2


def test_forfeit_streak_interrupted_by_normal_match() -> None:
    facts = newest_first(fact(0, B, forfeiter=A), fact(1, A), fact(2, B, forfeiter=A))
    assert evaluate_forfeit_series(facts, A, CFG.forfeit_series) is None


def test_forfeit_window_counts_across_opponents() -> None:
    facts = newest_first(
        fact(0, B, forfeiter=A), fact(1, C, forfeiter=A, pair=(A, C)), fact(2, B, forfeiter=A)
    )
    signal = evaluate_forfeit_window(facts, A, CFG.forfeit_series)
    assert signal is not None
    assert (signal.kind, signal.user_id, signal.other_user_id) == ("forfeit_window", A, None)
    assert signal.count == 3


def test_forfeit_window_ignores_old_and_foreign_forfeits() -> None:
    facts = newest_first(
        fact(0, B, forfeiter=A), fact(1, A, forfeiter=B), fact(200, B, forfeiter=A)
    )
    assert evaluate_forfeit_window(facts, A, CFG.forfeit_series) is None


def test_signal_key_is_stable_per_subject() -> None:
    first = evaluate_pair(newest_first(fact(0, A), fact(1, A), fact(2, A)), CFG.pair_repeat)
    second = evaluate_pair(newest_first(fact(5, B), fact(6, B), fact(7, A)), CFG.pair_repeat)
    assert first is not None
    assert second is not None
    assert first.key == second.key == f"pair_repeat:{A}:{B}"


def _event(
    *,
    ranked: bool = True,
    aborted: bool = False,
    players: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "match_id": str(uuid4()),
        "ranked": ranked,
        "ladder": "music.classic" if ranked else None,
        "aborted": aborted,
        "finished_at": T0.isoformat(),
        "players": players
        if players is not None
        else [
            {"user_id": str(B), "finish": "completed", "outcome": "win"},
            {"user_id": str(A), "finish": "forfeit", "outcome": "loss"},
        ],
    }


def test_fact_from_event_normalizes_pair_and_roles() -> None:
    result = fact_from_event(_event())
    assert result is not None
    assert (result.user_low, result.user_high) == (A, B)
    assert result.winner_id == B
    assert result.forfeiter_id == A


@pytest.mark.parametrize(
    "event",
    [
        _event(ranked=False),
        _event(aborted=True),
        _event(players=[{"user_id": str(A), "finish": "completed", "outcome": "win"}]),
        _event(
            players=[
                {"user_id": str(A), "finish": "completed", "outcome": None},
                {"user_id": str(B), "finish": "completed", "outcome": None},
                {"user_id": str(C), "finish": "completed", "outcome": None},
            ]
        ),
    ],
    ids=["not_ranked", "aborted", "solo", "party"],
)
def test_fact_from_event_skips_non_ranked_duels(event: dict[str, object]) -> None:
    assert fact_from_event(event) is None


def test_pair_of_orders_ids() -> None:
    assert pair_of(B, A) == (A, B)


@given(st.lists(st.integers(0, 50), min_size=0, max_size=12), st.integers(1, 6))
def test_more_matches_never_lowers_severity(minutes: list[int], min_matches: int) -> None:
    """Монотонность: добавление матча победителю не снимает и не ослабляет сигнал."""
    cfg = PairRepeatConfig(window_s=10**6, min_matches=min_matches, strong_streak=3)
    base = newest_first(*(fact(m, A) for m in minutes))
    extended = newest_first(*base, fact(60, A))
    before = evaluate_pair(base, cfg)
    after = evaluate_pair(extended, cfg)
    if before is not None:
        assert after is not None
        assert after.count > before.count
        assert after.streak >= before.streak
