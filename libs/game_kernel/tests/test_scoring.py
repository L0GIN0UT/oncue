"""Подсчёт очков ``by_level`` и ``by_speed`` (D10): примеры и свойства."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_game_kernel import ModeConfig, RoundOutcome, RoundResult
from gv_game_kernel.builtin import ByLevelParams, ByLevelScorer, BySpeedParams, BySpeedScorer
from gv_game_kernel.plugins import ParamIssue

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]

LEVEL = ByLevelScorer()
SPEED = BySpeedScorer()
CLASSIC = ByLevelParams(points=(6, 5, 4, 3, 2, 1), partial_ratio=0.5)
FAST = BySpeedParams(max_points=1000, min_points=100, window_s=20.0, partial_ratio=0.5)


def _exact(level: int, elapsed_ms: int | None = None, partial: int | None = None) -> RoundOutcome:
    return RoundOutcome(
        RoundResult.EXACT, level=level, partial_level=partial, elapsed_ms=elapsed_ms
    )


# ----------------------------------------------------------------------------- by_level
@pytest.mark.parametrize(
    ("outcome", "points"),
    [
        (_exact(1), 6),
        (_exact(6), 1),
        (_exact(3, partial=1), 4),  # max(4, floor(6 × 0.5) = 3)
        (_exact(6, partial=1), 3),  # частичный на 1-м дороже точного на 6-м
        (RoundOutcome(RoundResult.PARTIAL, partial_level=2), 2),  # floor(5 × 0.5)
        (RoundOutcome(RoundResult.GAVE_UP, partial_level=1), 3),
        (RoundOutcome(RoundResult.GAVE_UP), 0),
        (RoundOutcome(RoundResult.MISS), 0),
        (RoundOutcome(RoundResult.VOID), 0),
    ],
)
def test_by_level_examples(outcome: RoundOutcome, points: int) -> None:
    assert LEVEL.score(outcome, CLASSIC) == points


def test_by_level_ratio_is_exact_decimal() -> None:
    params = ByLevelParams(points=(100,), partial_ratio=0.29)
    assert LEVEL.score(RoundOutcome(RoundResult.PARTIAL, partial_level=1), params) == 29


def test_by_level_max_and_bad_level() -> None:
    assert LEVEL.max_score(CLASSIC, 6) == 6
    with pytest.raises(ValueError, match="вне таблицы"):
        LEVEL.score(_exact(7), CLASSIC)


def test_by_level_cross_check(seed_body: SeedBody) -> None:
    config = ModeConfig.model_validate(seed_body("music.daily.classic"))
    assert LEVEL.cross_check(CLASSIC, config=config, levels_count=6) == []
    bad = ByLevelParams(points=(5, 6, 1), partial_ratio=0.5)
    assert LEVEL.cross_check(bad, config=config, levels_count=6) == [
        ParamIssue("points", "config.levels_mismatch"),
        ParamIssue("points[1]", "config.points_increasing"),
    ]


@given(
    points=st.lists(st.integers(0, 1000), min_size=1, max_size=20).map(
        lambda xs: tuple(sorted(xs, reverse=True))
    ),
    ratio=st.floats(0, 1),
    data=st.data(),
)
def test_by_level_points_do_not_grow_with_level(
    points: tuple[int, ...], ratio: float, data: st.DataObject
) -> None:
    params = ByLevelParams(points=points, partial_ratio=ratio)
    a = data.draw(st.integers(1, len(points)))
    b = data.draw(st.integers(a, len(points)))
    assert LEVEL.score(_exact(a), params) >= LEVEL.score(_exact(b), params)
    partial = LEVEL.score(RoundOutcome(RoundResult.PARTIAL, partial_level=a), params)
    assert 0 <= partial <= LEVEL.score(_exact(a), params) <= LEVEL.max_score(params, len(points))


# ----------------------------------------------------------------------------- by_speed
@pytest.mark.parametrize(
    ("elapsed_ms", "points"),
    [
        (0, 1000),
        (5_000, 750),
        (10_000, 500),
        (19_000, 100),  # round(50) < min_points
        (60_000, 100),  # за окном — min_points
        (None, 100),  # без времени — min_points
        (10_001, 500),  # 499.95 → половина вверх
    ],
)
def test_by_speed_examples(elapsed_ms: int | None, points: int) -> None:
    assert SPEED.score(_exact(1, elapsed_ms), FAST) == points


def test_by_speed_partial_and_void() -> None:
    partial = RoundOutcome(RoundResult.PARTIAL, partial_level=1, elapsed_ms=10_000)
    assert SPEED.score(partial, FAST) == 250
    assert SPEED.score(RoundOutcome(RoundResult.VOID, elapsed_ms=0), FAST) == 0
    assert SPEED.score(RoundOutcome(RoundResult.MISS, elapsed_ms=0), FAST) == 0
    assert SPEED.max_score(FAST, 6) == 1000


def test_by_speed_cross_check(seed_body: SeedBody) -> None:
    config = ModeConfig.model_validate(seed_body("music.daily.classic"))
    bad = BySpeedParams(max_points=10, min_points=20, window_s=5.0, partial_ratio=0.5)
    assert SPEED.cross_check(bad, config=config, levels_count=6) == [
        ParamIssue("min_points", "config.min_above_max")
    ]
    assert SPEED.cross_check(FAST, config=config, levels_count=6) == []


@given(
    max_points=st.integers(1, 10_000),
    min_points=st.integers(0, 10_000),
    window=st.floats(0.001, 3600, allow_nan=False),
    t1=st.integers(0, 4_000_000),
    t2=st.integers(0, 4_000_000),
)
def test_by_speed_bounds_and_monotonic(
    max_points: int, min_points: int, window: float, t1: int, t2: int
) -> None:
    lo = min(min_points, max_points)
    params = BySpeedParams(max_points=max_points, min_points=lo, window_s=window, partial_ratio=0.5)
    fast, slow = sorted((t1, t2))
    a, b = SPEED.score(_exact(1, fast), params), SPEED.score(_exact(1, slow), params)
    assert lo <= b <= a <= max_points
