"""Scorer ``party_speed`` (A3, D4): ``max(1, round(max_points × k × clamp(1 − t/T)))``."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_game_kernel import RoundOutcome, RoundResult
from gv_game_kernel.builtin import PartySpeedParams, PartySpeedScorer

pytestmark = pytest.mark.unit

SCORER = PartySpeedScorer()
T = 20_000


def _params(max_points: int = 100, ratio: float = 0.5) -> PartySpeedParams:
    return PartySpeedParams(max_points=max_points, partial_ratio=ratio)


def _exact(t: int | None, window: int | None = T) -> RoundOutcome:
    return RoundOutcome(result=RoundResult.EXACT, level=1, elapsed_ms=t, window_ms=window)


def _partial(t: int | None, window: int | None = T) -> RoundOutcome:
    return RoundOutcome(result=RoundResult.PARTIAL, partial_level=1, elapsed_ms=t, window_ms=window)


@pytest.mark.parametrize(
    ("outcome", "points"),
    [
        (_exact(0), 100),
        (_exact(5_000), 75),
        (_exact(10_000), 50),
        (_exact(19_900), 1),  # 0.5 → округление вверх, но не меньше 1
        (_exact(20_000), 1),
        (_exact(60_000), 1),
        (_exact(None), 1),
        (_exact(1_000, None), 1),
        (_partial(0), 50),
        (_partial(10_000), 25),
        (_partial(19_999), 1),
        (RoundOutcome(result=RoundResult.MISS, elapsed_ms=0, window_ms=T), 0),
        (RoundOutcome(result=RoundResult.VOID), 0),
        (RoundOutcome(result=RoundResult.VOID, partial_level=1), 0),
        (RoundOutcome(result=RoundResult.GAVE_UP), 0),
    ],
)
def test_party_speed_table(outcome: RoundOutcome, points: int) -> None:
    assert SCORER.score(outcome, _params()) == points


def test_round_half_up_without_float_errors() -> None:
    # 100 × 0.29 × (1 − 0) = 29 ровно; 3 × 0.5 = 1.5 → 2.
    assert SCORER.score(_partial(0), _params(100, 0.29)) == 29
    assert SCORER.score(_partial(0), _params(3, 0.5)) == 2


def test_max_score_is_max_points() -> None:
    assert SCORER.max_score(_params(250), levels_count=6) == 250
    assert SCORER.presentation == "score"
    assert SCORER.requires_early_end is False


times = st.integers(min_value=0, max_value=3 * T)
max_points = st.integers(min_value=1, max_value=1000)
ratios = st.sampled_from([0.0, 0.1, 0.25, 0.3, 0.5, 0.75, 1.0])


@given(t=times, mp=max_points, ratio=ratios)
def test_guessed_round_scores_at_least_one(t: int, mp: int, ratio: float) -> None:
    p = _params(mp, ratio)
    assert SCORER.score(_exact(t), p) >= 1
    assert SCORER.score(_partial(t), p) >= 1
    assert SCORER.score(_exact(t), p) <= mp


@given(t1=times, t2=times, mp=max_points, ratio=ratios)
def test_monotone_non_increasing_in_time(t1: int, t2: int, mp: int, ratio: float) -> None:
    early, late = sorted((t1, t2))
    p = _params(mp, ratio)
    assert SCORER.score(_exact(early), p) >= SCORER.score(_exact(late), p)
    assert SCORER.score(_partial(early), p) >= SCORER.score(_partial(late), p)


@given(t=times, mp=max_points, ratio=ratios)
def test_partial_is_ratio_of_exact_formula(t: int, mp: int, ratio: float) -> None:
    from fractions import Fraction

    share = max(Fraction(0), 1 - Fraction(t, T))
    expected = max(1, int(mp * Fraction(str(ratio)) * share + Fraction(1, 2)))
    assert SCORER.score(_partial(t), _params(mp, ratio)) == expected
