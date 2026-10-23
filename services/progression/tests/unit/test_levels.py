"""Домен: уровень по накопленному опыту (ADR 0042)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain.config import LevelsConfig
from progression_service.domain.levels import (
    cost_to_reach,
    level_for_xp,
    next_level,
    progress,
    xp_for_level,
)

pytestmark = pytest.mark.unit

EXAMPLE = LevelsConfig(base=500, step=20, max_level=100)

curves = st.builds(
    LevelsConfig,
    base=st.integers(0, 5_000),
    step=st.integers(0, 500),
    max_level=st.integers(1, 200),
)


def test_first_level_starts_at_zero_and_second_costs_base_plus_step() -> None:
    assert xp_for_level(EXAMPLE, 1) == 0
    assert xp_for_level(EXAMPLE, 2) == 500 + 20 * 2
    assert level_for_xp(EXAMPLE, 0) == 1
    assert level_for_xp(EXAMPLE, 539) == 1
    assert level_for_xp(EXAMPLE, 540) == 2


def test_progress_inside_level_and_at_max() -> None:
    mid = progress(EXAMPLE, 540 + 100)
    assert (mid.level, mid.xp_in_level, mid.xp_to_next) == (2, 100, cost_to_reach(EXAMPLE, 3) - 100)
    top = progress(EXAMPLE, xp_for_level(EXAMPLE, 100) + 7)
    assert (top.level, top.xp_in_level, top.xp_to_next) == (100, 7, None)
    assert progress(EXAMPLE, 10**12).level == 100


def test_level_never_decreases_when_curve_gets_harder() -> None:
    harder = LevelsConfig(base=5000, step=100, max_level=100)
    assert level_for_xp(harder, 600) == 1
    assert next_level(harder, 7, 600) == 7
    assert progress(harder, 600, floor_level=7).level == 7


@given(curve=curves, xp=st.integers(0, 10**8), extra=st.integers(0, 10**6))
def test_level_is_monotonic_in_xp(curve: LevelsConfig, xp: int, extra: int) -> None:
    assert level_for_xp(curve, xp) <= level_for_xp(curve, xp + extra)


@given(curve=curves, n=st.integers(2, 200))
def test_threshold_is_sum_of_transition_costs(curve: LevelsConfig, n: int) -> None:
    assert xp_for_level(curve, n) == sum(cost_to_reach(curve, k) for k in range(2, n + 1))


@given(curve=curves, xp=st.integers(0, 10**8))
def test_level_matches_thresholds(curve: LevelsConfig, xp: int) -> None:
    level = level_for_xp(curve, xp)
    assert 1 <= level <= curve.max_level
    assert xp_for_level(curve, level) <= xp
    if level < curve.max_level:
        assert xp < xp_for_level(curve, level + 1)


@given(curve=curves, floor=st.integers(1, 300), xp=st.integers(0, 10**7))
def test_next_level_is_never_below_current(curve: LevelsConfig, floor: int, xp: int) -> None:
    assert next_level(curve, floor, xp) >= floor
