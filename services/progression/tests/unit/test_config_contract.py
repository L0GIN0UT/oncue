"""Контракт конфига progression (ADR 0042–0044): схема валидна, пример проходит, ошибки ловятся."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.unit

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "progression"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    return data


def _validator() -> Draft202012Validator:
    schema = _load("progression_config.v1.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_example_config_is_valid() -> None:
    assert list(_validator().iter_errors(_load("examples/progression_config.v1.json"))) == []


def test_example_follows_owner_decisions() -> None:
    config = _load("examples/progression_config.v1.json")
    assert config["levels"] == {"max_level": 100, "base": 500, "step": 20}
    assert config["quests"]["daily_count"] == 3
    assert config["quests"]["weekly_count"] == 10
    assert len(config["wheel"]["sectors"]) == 12
    assert [b["to"] for b in config["verses"]["weekly_top"]][-1] == 100
    assert [b["to"] for b in config["verses"]["season"]][-1] == 1000


def _drop_levels_base(c: dict[str, Any]) -> None:
    del c["levels"]["base"]


def _negative_amount(c: dict[str, Any]) -> None:
    c["xp"]["duel"]["win_bonus"] = -1


def _zero_weight(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][0]["weight"] = 0


def _fractional_verses(c: dict[str, Any]) -> None:
    c["verses"]["daily"] = 2.5


def _ratio_above_one(c: dict[str, Any]) -> None:
    c["min_rounds_ratio"] = 1.5


def _unknown_field(c: dict[str, Any]) -> None:
    c["xp"]["solo_round"]["bonus"] = 1


def _boost_multiplier_not_above_one(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][6]["prize"]["multiplier"] = 1


def _one_sector(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"] = c["wheel"]["sectors"][:1]


def _bad_key(c: dict[str, Any]) -> None:
    c["badges"][0]["key"] = "First Win"


def _negative_duplicate_verses(c: dict[str, Any]) -> None:
    c["shop"]["rarities"][0]["duplicate_verses"] = -1


def _animated_frames_below_two(c: dict[str, Any]) -> None:
    c["shop"]["media"]["animated_max_frames"] = 1


def _no_slots(c: dict[str, Any]) -> None:
    c["shop"]["slots"] = []


def _unknown_bucket_in_spend_order(c: dict[str, Any]) -> None:
    c["spend"]["bucket_order"] = ["earned", "gold"]


def _duplicate_bucket_in_spend_order(c: dict[str, Any]) -> None:
    c["spend"]["bucket_order"] = ["earned", "earned"]


def _zero_promo_attempts(c: dict[str, Any]) -> None:
    c["promo"]["attempts_per_ip"] = 0


def _short_promo_code(c: dict[str, Any]) -> None:
    c["promo"]["code_length"] = 4


def _spend_order_without_earned(c: dict[str, Any]) -> None:
    c["spend"]["bucket_order"] = ["paid"]  # в 5b списывается earned (ADR 0043)


def _promo_without_batch_cap(c: dict[str, Any]) -> None:
    del c["promo"]["max_batch_verses"]


def _promo_without_actor_daily_cap(c: dict[str, Any]) -> None:
    del c["promo"]["max_daily_verses_per_actor"]


def _promo_without_total_daily_cap(c: dict[str, Any]) -> None:
    del c["promo"]["max_daily_verses_total"]


def _promo_zero_daily_cap(c: dict[str, Any]) -> None:
    c["promo"]["max_daily_verses_per_actor"] = 0


def _promo_daily_cap_above_bound(c: dict[str, Any]) -> None:
    c["promo"]["max_daily_verses_total"] = 1_000_000_001


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_levels_base,
        _negative_amount,
        _zero_weight,
        _fractional_verses,
        _ratio_above_one,
        _unknown_field,
        _boost_multiplier_not_above_one,
        _one_sector,
        _bad_key,
        _negative_duplicate_verses,
        _animated_frames_below_two,
        _no_slots,
        _unknown_bucket_in_spend_order,
        _duplicate_bucket_in_spend_order,
        _zero_promo_attempts,
        _short_promo_code,
        _spend_order_without_earned,
        _promo_without_batch_cap,
        _promo_without_actor_daily_cap,
        _promo_without_total_daily_cap,
        _promo_zero_daily_cap,
        _promo_daily_cap_above_bound,
    ],
)
def test_invalid_config_is_rejected(mutate: Callable[[dict[str, Any]], None]) -> None:
    config = copy.deepcopy(_load("examples/progression_config.v1.json"))
    mutate(config)
    assert list(_validator().iter_errors(config)) != []


def test_example_shop_keys_are_unique() -> None:
    """Схема не умеет уникальность по полю: ключи слотов и редкостей проверяет домен (S1)."""
    shop = _load("examples/progression_config.v1.json")["shop"]
    for section in ("slots", "rarities"):
        keys = [entry["key"] for entry in shop[section]]
        assert len(keys) == len(set(keys)), section


def test_config_without_5b_sections_stays_valid() -> None:
    """Секции 5b необязательны: версии конфига 5a (сид, архив) остаются допустимыми."""
    config = copy.deepcopy(_load("examples/progression_config.v1.json"))
    for section in ("shop", "promo", "spend"):
        del config[section]
    assert list(_validator().iter_errors(config)) == []
