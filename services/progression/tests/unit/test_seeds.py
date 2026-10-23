"""Сид конфига (`seeds/progression/config.yaml`): схема, домен, пулы заданий, колесо."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from progression_service.domain import metrics
from progression_service.domain.config import ProgressionConfig

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
SEED = ROOT / "seeds" / "progression" / "config.yaml"


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEED.read_text("utf-8"))
    return data


def test_seed_matches_schema_and_domain(raw: dict[str, Any]) -> None:
    schema = json.loads(
        (ROOT / "contracts" / "progression" / "progression_config.v1.json").read_text("utf-8")
    )

    assert list(Draft202012Validator(schema).iter_errors(raw)) == []
    ProgressionConfig.parse(raw)


def test_owner_values(raw: dict[str, Any]) -> None:
    assert raw["levels"] == {"max_level": 100, "base": 500, "step": 20}
    assert raw["min_rounds_ratio"] == 0.5
    assert raw["timezone"] == "Europe/Moscow"
    assert raw["verses"]["online_daily_cap"] == 200
    assert raw["wheel"]["max_boost_queue"] == 3
    assert "online_daily_cap" not in raw["xp"]  # у опыта онлайна потолка нет


def test_quest_pools_cover_counts(raw: dict[str, Any]) -> None:
    quests = raw["quests"]
    enabled = [q for q in quests["items"] if q["enabled"]]

    def count(period: str) -> int:
        return sum(1 for q in enabled if q["period"] == period)

    assert count("daily") >= quests["daily_count"] == 3
    assert count("weekly") >= quests["weekly_count"] == 10
    assert {"first_duel", "first_collection"} <= {
        q["key"] for q in enabled if q["period"] == "one_time"
    }
    assert {q["metric"] for q in quests["items"]} | {b["metric"] for b in raw["badges"]} <= (
        metrics.KNOWN
    )


def test_keys_unique(raw: dict[str, Any]) -> None:
    for keys in (
        [q["key"] for q in raw["quests"]["items"]],
        [b["key"] for b in raw["badges"]],
        [s["key"] for s in raw["wheel"]["sectors"]],
    ):
        assert len(keys) == len(set(keys))


def test_wheel_has_twelve_sectors_with_positive_weights(raw: dict[str, Any]) -> None:
    sectors = raw["wheel"]["sectors"]

    assert len(sectors) == 12
    assert sum(s["weight"] for s in sectors) > 0
    assert {s["prize"]["kind"] for s in sectors} == {"xp", "verses", "boost", "item", "none"}
    assert [s["prize"]["item_key"] for s in sectors if s["prize"]["kind"] == "item"] == [
        "wheel_cosmetic"
    ]
