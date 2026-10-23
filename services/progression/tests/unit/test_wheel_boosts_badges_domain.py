"""Домен колеса, бустов и бейджей."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import badges, boosts, wheel
from progression_service.domain.config import BadgeDef, Band, PrizeDef, PrizeKind, SectorDef

from gv_testkit import FakeRandom

pytestmark = pytest.mark.unit

MSK = ZoneInfo("Europe/Moscow")
NONE = PrizeDef(PrizeKind.NONE, 0, None, None, None)


def sectors(*weights: int) -> list[SectorDef]:
    return [SectorDef(f"s{i}", f"s{i}", w, NONE) for i, w in enumerate(weights)]


# ---------------------------------------------------------------- колесо
def test_chances_are_weight_over_sum() -> None:
    assert wheel.chances(sectors(1, 1, 2)) == [0.25, 0.25, 0.5]


def test_pick_follows_cumulative_weights() -> None:
    class Fixed(FakeRandom):
        def __init__(self, roll: int) -> None:
            super().__init__()
            self.roll = roll

        def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
            assert (start, stop) == (4, None)  # сумма весов
            return self.roll

    secs = sectors(1, 1, 2)
    assert [wheel.pick_sector(secs, Fixed(r)).key for r in range(4)] == ["s0", "s1", "s2", "s2"]


def test_distribution_on_fixed_seed_matches_weights() -> None:
    secs = sectors(18, 10, 4, 1)
    rng = FakeRandom(seed=42)
    runs = 20_000
    seen = Counter(wheel.pick_sector(secs, rng).key for _ in range(runs))
    for sector, chance in zip(secs, wheel.chances(secs), strict=True):
        assert abs(seen[sector.key] / runs - chance) < 0.02


@given(weights=st.lists(st.integers(1, 1000), min_size=2, max_size=12), seed=st.integers())
def test_pick_always_returns_a_sector(weights: list[int], seed: int) -> None:
    secs = sectors(*weights)
    assert wheel.pick_sector(secs, FakeRandom(seed)) in secs
    assert sum(wheel.chances(secs)) == pytest.approx(1)


def test_day_and_next_spin_follow_timezone() -> None:
    late = datetime(2026, 10, 23, 21, 30, tzinfo=UTC)  # 00:30 24 октября в Москве
    assert wheel.wheel_day(late, MSK) == date(2026, 10, 24)
    assert wheel.next_spin_at(date(2026, 10, 24), MSK) == datetime(2026, 10, 24, 21, 0, tzinfo=UTC)


# ---------------------------------------------------------------- бусты
T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
USER = UUID(int=1)


def boost(n: int, left: int) -> boosts.Boost:
    return boosts.Boost(UUID(int=n), USER, Decimal(2), left, T0 + timedelta(minutes=n))


def test_active_is_earliest_with_matches_left() -> None:
    items = [boost(3, 2), boost(1, 0), boost(2, 5)]
    assert boosts.active(items) == items[2]
    assert boosts.queued(items) == 1
    assert boosts.active([boost(1, 0)]) is None
    assert boosts.queued([]) == 0


def test_queue_limit() -> None:
    assert boosts.can_enqueue([], 1)
    assert boosts.can_enqueue([boost(1, 3)], 1)  # активный + 0 в очереди
    assert not boosts.can_enqueue([boost(1, 3), boost(2, 3)], 1)
    assert boosts.can_enqueue([boost(1, 3), boost(2, 3)], 2)


# ---------------------------------------------------------------- бейджи
def badge(key: str, metric: str, target: int, *, enabled: bool = True) -> BadgeDef:
    return BadgeDef(key, key, "", metric, target, enabled)


def test_badges_unlock_once_when_target_reached() -> None:
    cfg = [
        badge("a", "wins", 1),
        badge("b", "wins", 5),
        badge("c", "rounds_played", 1, enabled=False),
    ]
    assert [b.key for b in badges.newly_unlocked(cfg, {"wins": 1}, set())] == ["a"]
    assert badges.newly_unlocked(cfg, {"wins": 1}, {"a"}) == []
    assert [b.key for b in badges.newly_unlocked(cfg, {"wins": 9}, {"a"})] == ["b"]
    assert badges.newly_unlocked(cfg, {"rounds_played": 9}, set()) == []  # выключен


def test_season_tier_from_bands() -> None:
    bands = (Band(1, 1, 10), Band(2, 3, 5), Band(4, 10, 1))
    assert [badges.season_tier(bands, p) for p in (1, 2, 3, 4, 10, 11)] == [3, 2, 2, 1, 1, 0]
