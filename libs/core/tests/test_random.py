"""Порт Random: детерминизм SeededRandom и совместимость реализаций с протоколом."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_core import NotImplementedYetError, Random, SeededRandom, SystemRandom

pytestmark = pytest.mark.unit


def _draw(rng: Random) -> tuple[int, list[int], list[int], float]:
    items = list(range(10))
    rng.shuffle(items)
    return rng.randrange(0, 1000), rng.sample(range(100), 5), items, rng.random()


@given(st.binary(min_size=1, max_size=64))
def test_same_seed_same_sequence(seed: bytes) -> None:
    assert _draw(SeededRandom(seed)) == _draw(SeededRandom(seed))


def test_different_seeds_differ() -> None:
    assert _draw(SeededRandom(b"music.daily.classic|2026-10-10")) != _draw(
        SeededRandom(b"music.daily.classic|2026-10-11")
    )


def test_seeded_sequence_is_stable_across_releases() -> None:
    """Golden: смена алгоритма перемешает уже созданные наборы дня — только осознанно."""
    assert SeededRandom(b"golden").sample(range(100), 5) == [20, 94, 10, 25, 79]


def test_empty_seed_is_rejected() -> None:
    with pytest.raises(ValueError, match="seed"):
        SeededRandom(b"")


def test_system_random_satisfies_port() -> None:
    rng: Random = SystemRandom()
    value, sample, items, fraction = _draw(rng)
    assert 0 <= value < 1000
    assert len(set(sample)) == 5
    assert sorted(items) == list(range(10))
    assert 0.0 <= fraction < 1.0


def test_not_implemented_code() -> None:
    assert NotImplementedYetError("позже").code == "common.not_implemented"
