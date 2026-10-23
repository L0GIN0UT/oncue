"""Домен ladder-а: разбор конфига по контракту, звания, калибровка (ADR 0037)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from rating_service.domain.errors import LadderInvalidError
from rating_service.domain.ladder import LadderConfig

pytestmark = pytest.mark.unit

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "rating"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    return data


def example() -> dict[str, Any]:
    return copy.deepcopy(_load("examples/ladder.film.v1.json"))


def test_example_is_parsed_and_roundtrips() -> None:
    raw = example()
    config = LadderConfig.parse(raw)
    assert config.key == "film"
    assert config.to_dict() == raw
    assert list(Draft202012Validator(_load("ladder.v1.json")).iter_errors(config.to_dict())) == []


def test_tier_boundaries_are_strict_upper_bounds() -> None:
    config = LadderConfig.parse(example())
    tiers = config.tiers
    for lower, upper in pairwise(tiers):
        assert lower.below is not None
        assert config.tier_for(lower.below - 1).key == lower.key
        assert config.tier_for(lower.below).key == upper.key
    assert config.tier_for(-10_000).key == tiers[0].key
    assert config.tier_for(10**9).key == tiers[-1].key


def test_next_tier_at_is_upper_bound_of_current() -> None:
    config = LadderConfig.parse(example())
    first = config.tiers[0]
    assert first.below is not None
    assert config.next_tier_at(first.below - 1) == first.below
    assert config.next_tier_at(10**9) is None


def test_calibration_counts_from_config() -> None:
    raw = example()
    raw["calibration"]["matches"] = 3
    config = LadderConfig.parse(raw)
    assert [config.calibration_left(n) for n in range(5)] == [3, 2, 1, 0, 0]
    assert config.is_calibrating(2)
    assert not config.is_calibrating(3)


def _bad_tier_order(c: dict[str, Any]) -> None:
    c["tiers"][1]["below"] = c["tiers"][0]["below"]


def _open_tier_in_the_middle(c: dict[str, Any]) -> None:
    c["tiers"][2]["below"] = None


def _closed_last_tier(c: dict[str, Any]) -> None:
    c["tiers"][-1]["below"] = 99999


def _duplicate_tier_key(c: dict[str, Any]) -> None:
    c["tiers"][1]["key"] = c["tiers"][0]["key"]


def _lite_multiplier_missing(c: dict[str, Any]) -> None:
    del c["elo"]["format_multiplier"]["lite"]


def _window_max_below_base(c: dict[str, Any]) -> None:
    c["matchmaking"]["window"]["max"] = c["matchmaking"]["window"]["base"] - 1


def _unknown_timezone(c: dict[str, Any]) -> None:
    c["weekly"]["timezone"] = "Mars/Olympus"


def _drop_start(c: dict[str, Any]) -> None:
    del c["elo"]["start"]


def _party_multiplier(c: dict[str, Any]) -> None:
    c["elo"]["format_multiplier"]["party"] = 1.0


def _zero_multiplier(c: dict[str, Any]) -> None:
    c["elo"]["format_multiplier"]["lite"] = 0


def _bad_domain(c: dict[str, Any]) -> None:
    c["domain"] = "anime"


def _extra_field(c: dict[str, Any]) -> None:
    c["surprise"] = 1


def _bool_as_number(c: dict[str, Any]) -> None:
    c["elo"]["k"] = True


def _ratio_above_one(c: dict[str, Any]) -> None:
    c["season"]["soft_reset"]["ratio"] = 1.5


def _no_tiers(c: dict[str, Any]) -> None:
    c["tiers"] = []


BROKEN = [
    _bad_tier_order,
    _open_tier_in_the_middle,
    _closed_last_tier,
    _duplicate_tier_key,
    _lite_multiplier_missing,
    _window_max_below_base,
    _unknown_timezone,
    _drop_start,
    _party_multiplier,
    _zero_multiplier,
    _bad_domain,
    _extra_field,
    _bool_as_number,
    _ratio_above_one,
    _no_tiers,
]


@pytest.mark.parametrize("patch", BROKEN, ids=[p.__name__ for p in BROKEN])
def test_broken_config_is_rejected_with_paths(patch: Callable[[dict[str, Any]], None]) -> None:
    raw = example()
    patch(raw)
    with pytest.raises(LadderInvalidError) as caught:
        LadderConfig.parse(raw)
    errors = caught.value.details["errors"]
    assert errors
    assert all(set(e) == {"path", "message"} for e in errors)
    assert caught.value.code == "rating.ladder_invalid"


def test_everything_json_schema_rejects_is_rejected_by_domain() -> None:
    validator = Draft202012Validator(_load("ladder.v1.json"))
    for patch in BROKEN:
        raw = example()
        patch(raw)
        if list(validator.iter_errors(raw)):
            with pytest.raises(LadderInvalidError):
                LadderConfig.parse(raw)


def test_non_object_is_rejected() -> None:
    with pytest.raises(LadderInvalidError):
        LadderConfig.parse([])  # type: ignore[arg-type]  # защита от неверного типа тела


@given(rating=st.integers(min_value=-5000, max_value=10_000))
def test_tier_is_monotonic_in_rating(rating: int) -> None:
    config = LadderConfig.parse(example())
    keys = [t.key for t in config.tiers]
    assert keys.index(config.tier_for(rating).key) <= keys.index(config.tier_for(rating + 1).key)


# ------------------------------------------------------------------ границы значений (S1)
def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def patch(c: dict[str, Any]) -> None:
        target = c
        *head, last = path.split("/")
        for part in head:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[int(last) if isinstance(target, list) else last] = value

    patch.__name__ = f"{path}={value!r}"[:60]
    return patch


def _too_many_tiers(c: dict[str, Any]) -> None:
    base = c["tiers"][0]
    c["tiers"] = [{**base, "key": f"t{i}", "below": i + 1} for i in range(100)] + [
        {**base, "key": "top", "below": None}
    ]


def _too_many_formats(c: dict[str, Any]) -> None:
    c["formats"] = [f"f{i}" for i in range(11)]


BOUNDS = [
    _set("elo/k", 0.5),
    _set("elo/k", 1001),
    _set("elo/k_calibrating", 0),
    _set("elo/k_calibrating", 1001),
    _set("elo/scale", 0.5),
    _set("elo/scale", 100_001),
    _set("elo/start", 1_000_001),
    _set("elo/start", -1_000_001),
    _set("elo/leaver_penalty", 100_001),
    _set("elo/format_multiplier/classic", 101),
    _set("calibration/matches", 10_001),
    _set("tiers/0/below", 1_000_001),
    _set("season/length_days", 367),
    _set("season/soft_reset/target", 1_000_001),
    _set("matchmaking/window/max", 1_000_001),
    _set("matchmaking/window/step_every_s", 86_401),
    _set("matchmaking/slow_hint_after_s", 86_401),
    _set("matchmaking/interval_s", 3_601),
    _set("leaderboard/min_matches", 100_001),
    _too_many_tiers,
    _too_many_formats,
]
NON_JSON = [
    _set("elo/k", float("nan")),
    _set("elo/k", float("inf")),
    _set("elo/scale", float("inf")),
    _set("elo/format_multiplier/lite", float("nan")),
    _set("season/soft_reset/ratio", float("nan")),
]


@pytest.mark.parametrize("patch", BOUNDS + NON_JSON, ids=lambda p: p.__name__)
def test_out_of_bounds_values_are_rejected_by_domain(
    patch: Callable[[dict[str, Any]], None],
) -> None:
    raw = example()
    patch(raw)
    with pytest.raises(LadderInvalidError):
        LadderConfig.parse(raw)


@pytest.mark.parametrize("patch", BOUNDS, ids=lambda p: p.__name__)
def test_out_of_bounds_values_are_rejected_by_json_schema(
    patch: Callable[[dict[str, Any]], None],
) -> None:
    raw = example()
    patch(raw)
    assert list(Draft202012Validator(_load("ladder.v1.json")).iter_errors(raw)) != []


def test_boundary_values_are_accepted() -> None:
    raw = example()
    for patch in (
        _set("elo/k", 1),
        _set("elo/k", 1000),
        _set("elo/scale", 1),
        _set("elo/start", 1_000_000),
        _set("season/length_days", 366),
        _set("calibration/matches", 0),
    ):
        patch(raw)
    LadderConfig.parse(raw)
    assert list(Draft202012Validator(_load("ladder.v1.json")).iter_errors(raw)) == []


def test_formats_are_not_hardcoded() -> None:
    raw = example()
    raw["formats"] = ["classic", "blitz"]
    raw["elo"]["format_multiplier"] = {"classic": 1.0, "blitz": 0.5}
    config = LadderConfig.parse(raw)
    assert config.formats == ("classic", "blitz")
    raw["elo"]["format_multiplier"]["rapid"] = 1.0
    with pytest.raises(LadderInvalidError):
        LadderConfig.parse(raw)


def test_huge_rating_gap_does_not_overflow() -> None:
    from rating_service.domain.elo import EloCalculator

    config = LadderConfig.parse(example())
    calc = EloCalculator(config.elo, config.calibration)
    assert 0.0 <= calc.expected(-1_000_000, 1_000_000) <= 1.0
    assert 0.0 <= calc.expected(1_000_000, -1_000_000) <= 1.0
