"""Сиды ladder-ов (`seeds/rating/ladders`, ADR 0037): схема, домен, звания, значения владельца."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from rating_service.domain.ladder import LadderConfig

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
SEEDS = ROOT / "seeds" / "rating" / "ladders"
TOP_TITLES = {"film": "rating.tier.cinephile", "music": "rating.tier.melomaniac"}


def load(key: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((SEEDS / f"{key}.yaml").read_text("utf-8"))
    return data


def test_both_ladders_are_seeded() -> None:
    assert sorted(p.stem for p in SEEDS.glob("*.yaml")) == ["film", "music"]


@pytest.mark.parametrize("key", ["film", "music"])
def test_seed_matches_schema_and_domain_rules(key: str) -> None:
    schema = json.loads((ROOT / "contracts" / "rating" / "ladder.v1.json").read_text("utf-8"))
    raw = load(key)

    assert list(Draft202012Validator(schema).iter_errors(raw)) == []
    config = LadderConfig.parse(raw)
    assert (config.key, config.domain) == (key, key)


@pytest.mark.parametrize("key", ["film", "music"])
def test_owner_defaults(key: str) -> None:
    raw = load(key)

    assert raw["elo"] | {"format_multiplier": None} == {
        "start": 1000,
        "k_calibrating": 40,
        "k": 20,
        "scale": 400,
        "leaver_penalty": 10,
        "format_multiplier": None,
    }
    assert raw["elo"]["format_multiplier"] == {"classic": 1.0, "lite": 0.8}
    assert raw["formats"] == ["classic", "lite"]
    assert raw["calibration"] == {"matches": 10, "hide_tier": True}
    assert raw["matchmaking"]["window"] == {"base": 100, "step": 50, "step_every_s": 10, "max": 600}
    assert raw["season"] == {"length_days": 56, "soft_reset": {"target": 1000, "ratio": 0.5}}
    assert raw["weekly"]["timezone"] == "Europe/Moscow"


@pytest.mark.parametrize("key", ["film", "music"])
def test_tiers_are_three_steps_and_open_top(key: str) -> None:
    tiers = load(key)["tiers"]

    families = [t["family"] for t in tiers[:-1]]
    assert list(dict.fromkeys(families)) == [
        "iron", "bronze", "silver", "gold", "platinum", "diamond",
    ]  # fmt: skip
    assert all(families.count(f) == 3 for f in set(families))
    assert [t["step"] for t in tiers[:-1]] == [1, 2, 3] * 6
    top = tiers[-1]
    assert (top["below"], top["step"], top["family"]) == (None, None, "top")
    assert top["title_key"] == TOP_TITLES[key]
