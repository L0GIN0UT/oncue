"""Подбор рейтинговой дуэли (ADR 0038): окно, жадный батч, мягкий сброс. Без IO."""

from __future__ import annotations

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from match_service.domain.queue import (
    Candidate,
    MatchmakingConfig,
    pair_batch,
    queue_format_of,
    soft_reset,
)

pytestmark = pytest.mark.unit

CONFIG = MatchmakingConfig(
    base=100, step=50, step_every_s=10, max=300, slow_hint_after_s=60, interval_s=1
)
NOW = 1_000_000_000


def _c(n: int, rating: int, waited_s: int = 0) -> Candidate:
    return Candidate(UUID(int=n), rating, NOW - waited_s * 1000)


def test_window_grows_by_step_up_to_max() -> None:
    assert CONFIG.window_at(0) == 100
    assert CONFIG.window_at(9) == 100
    assert CONFIG.window_at(10) == 150
    assert CONFIG.window_at(35) == 250
    assert CONFIG.window_at(10_000) == 300


def test_slow_hint_after_threshold() -> None:
    assert not CONFIG.is_slow(59)
    assert CONFIG.is_slow(60)


def test_pair_requires_both_windows() -> None:
    # Разница 140: окно новичка ±100 не достаёт, окно ждущего 20 с (±200) — достаёт.
    assert pair_batch([_c(1, 1000), _c(2, 1140, waited_s=20)], CONFIG, NOW) == []
    both = pair_batch([_c(1, 1000, waited_s=10), _c(2, 1140, waited_s=20)], CONFIG, NOW)
    assert len(both) == 1


def test_pair_picks_closest_and_host_is_the_longest_waiting() -> None:
    a, b, c = _c(1, 1000, 5), _c(2, 1030, 30), _c(3, 1500)
    [(first, second)] = pair_batch([c, a, b], CONFIG, NOW)
    assert (first, second) == (b, a)


def test_window_never_exceeds_max_even_for_long_waiters() -> None:
    far = [_c(1, 1000, 9999), _c(2, 1400, 9999)]
    assert pair_batch(far, CONFIG, NOW) == []


def test_soft_reset_formula() -> None:
    assert soft_reset(1400, target=1000, ratio=0.5) == 1200
    assert soft_reset(800, target=1000, ratio=0.5) == 900
    assert soft_reset(1000, target=1000, ratio=0.5) == 1000
    assert soft_reset(1001, target=1000, ratio=0.5) == 1001  # 1000.5 округляется вверх


def test_queue_format_maps_back_from_answer_format() -> None:
    assert queue_format_of("classic") == "classic"
    assert queue_format_of("pick") == "lite"
    assert queue_format_of("free") is None


candidates = st.lists(
    st.builds(
        _c,
        st.integers(1, 40),
        st.integers(0, 3000),
        st.integers(0, 200),
    ),
    unique_by=lambda c: c.user_id,
    max_size=40,
)


@given(candidates)
def test_batch_pairs_are_disjoint_and_inside_both_windows(people: list[Candidate]) -> None:
    pairs = pair_batch(people, CONFIG, NOW)
    seen: set[UUID] = set()
    for a, b in pairs:
        assert a.user_id not in seen
        assert b.user_id not in seen
        seen.update((a.user_id, b.user_id))
        diff = abs(a.rating - b.rating)
        assert diff <= CONFIG.window_at(a.wait_s(NOW))
        assert diff <= CONFIG.window_at(b.wait_s(NOW))
        assert a.entered_at_ms <= b.entered_at_ms


@given(candidates)
def test_batch_is_deterministic_and_order_independent(people: list[Candidate]) -> None:
    assert pair_batch(people, CONFIG, NOW) == pair_batch(list(reversed(people)), CONFIG, NOW)


@given(candidates)
def test_batch_leaves_no_matchable_pair_unmatched(people: list[Candidate]) -> None:
    """Жадность не оставляет двоих свободных, которые могли бы сыграть друг с другом."""
    taken = {p.user_id for pair in pair_batch(people, CONFIG, NOW) for p in pair}
    free = [p for p in people if p.user_id not in taken]
    for i, a in enumerate(free):
        for b in free[i + 1 :]:
            diff = abs(a.rating - b.rating)
            assert diff > min(CONFIG.window_at(a.wait_s(NOW)), CONFIG.window_at(b.wait_s(NOW)))


@given(st.integers(0, 4000), st.integers(0, 4000), st.floats(0, 1))
def test_soft_reset_moves_toward_target_and_never_overshoots(
    old: int, target: int, ratio: float
) -> None:
    new = soft_reset(old, target=target, ratio=ratio)
    assert min(old, target) <= new <= max(old, target)
