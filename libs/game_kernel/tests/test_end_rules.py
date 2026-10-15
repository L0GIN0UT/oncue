"""Правила завершения сессии (``all_rounds``, ``lives``) и очки ``streak``/``none``."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_game_kernel import ModeEngine, RoundOutcome, RoundResult
from gv_game_kernel.builtin import (
    AllRoundsEnd,
    AllRoundsParams,
    ByLevelScorer,
    LivesEnd,
    LivesParams,
    NoneParams,
    NoneScorer,
    StreakParams,
    StreakScorer,
)

pytestmark = pytest.mark.unit

EXACT = RoundOutcome(RoundResult.EXACT, level=1)
MISS = RoundOutcome(RoundResult.MISS)
PARTIAL = RoundOutcome(RoundResult.PARTIAL, partial_level=1)
GAVE_UP = RoundOutcome(RoundResult.GAVE_UP)
VOID = RoundOutcome(RoundResult.VOID)

outcomes = st.lists(st.sampled_from([EXACT, MISS, PARTIAL, GAVE_UP, VOID]), max_size=30)


def test_lives_count_every_lost_round_but_void() -> None:
    rule, params = LivesEnd(), LivesParams(count=3)
    assert rule.ends_early
    assert rule.lives_left([], params) == 3
    assert rule.lives_left([EXACT, VOID, MISS], params) == 2
    assert not rule.should_end([MISS, PARTIAL], params)
    assert rule.should_end([MISS, PARTIAL, GAVE_UP], params)
    assert rule.lives_left([MISS] * 5, params) == 0


def test_all_rounds_never_ends_early() -> None:
    rule = AllRoundsEnd()
    assert not rule.ends_early
    assert not rule.should_end([MISS] * 10, AllRoundsParams())
    assert rule.lives_left([MISS], AllRoundsParams()) is None


@given(history=outcomes, count=st.integers(min_value=1, max_value=10))
def test_lives_end_exactly_when_losses_reach_count(history: list[RoundOutcome], count: int) -> None:
    params = LivesParams(count=count)
    losses = sum(1 for o in history if o.result not in (RoundResult.EXACT, RoundResult.VOID))
    assert LivesEnd().should_end(history, params) == (losses >= count)
    assert LivesEnd().lives_left(history, params) == max(0, count - losses)


@given(history=outcomes)
def test_streak_equals_exact_count_until_first_miss(history: list[RoundOutcome]) -> None:
    """Серия с lives:1: сессия идёт до первого проигрыша, очки = число exact до него."""
    played: list[RoundOutcome] = []
    for outcome in history:
        played.append(outcome)
        if LivesEnd().should_end(played, LivesParams(count=1)):
            break
    scorer, params = StreakScorer(), StreakParams(points_per_round=1)
    exact_before_miss = 0
    for outcome in played:
        if outcome.result not in (RoundResult.EXACT, RoundResult.VOID):
            break
        exact_before_miss += outcome.result is RoundResult.EXACT
    assert sum(scorer.score(o, params) for o in played) == exact_before_miss
    # Текущая серия: сессия ещё идёт — все exact; закончилась проигрышем — 0.
    alive = all(o.result in (RoundResult.EXACT, RoundResult.VOID) for o in played)
    assert ModeEngine.streak(played) == (exact_before_miss if alive else 0)


def test_streak_scorer_points_and_presentation() -> None:
    scorer, params = StreakScorer(), StreakParams(points_per_round=10)
    assert scorer.presentation == "streak" and scorer.requires_early_end
    assert [scorer.score(o, params) for o in (EXACT, MISS, PARTIAL, VOID)] == [10, 0, 0, 0]
    assert scorer.max_score(params, 1) == 10
    assert ByLevelScorer.presentation == "score" and not ByLevelScorer.requires_early_end


def test_none_scorer_is_bracket_without_points() -> None:
    scorer, params = NoneScorer(), NoneParams()
    assert scorer.presentation == "bracket" and not scorer.requires_early_end
    assert scorer.score(EXACT, params) == 0 and scorer.max_score(params, 1) == 0


def test_engine_streak_skips_void_and_stops_at_loss() -> None:
    assert ModeEngine.streak([]) == 0
    assert ModeEngine.streak([MISS, EXACT, VOID, EXACT]) == 2
    assert ModeEngine.streak([EXACT, EXACT, MISS]) == 0
