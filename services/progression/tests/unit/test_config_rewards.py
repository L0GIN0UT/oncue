"""Домен: проверка блоков заданий, бейджей и колеса конфига (P2; ``progression.config_invalid``)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from progression_service.domain.config import ConfigInvalidError, ProgressionConfig

pytestmark = pytest.mark.unit

Make = Callable[..., dict[str, Any]]


def test_example_blocks_are_parsed(make_config: Make) -> None:
    cfg = ProgressionConfig.parse(make_config())
    assert [q.key for q in cfg.quests.pool("daily")] == ["play_3_solo", "guess_10", "daily_done"]
    assert cfg.quests.pool("one_time")[0].reward.xp == 100
    assert {b.metric for b in cfg.badges} >= {"wins", "daily_streak", "season_tier"}
    assert len(cfg.wheel.sectors) == 12
    assert cfg.wheel.max_boost_queue == 3
    boost = next(s for s in cfg.wheel.sectors if s.key == "boost_x2_3").prize
    assert (boost.multiplier, boost.matches) == (Decimal(2), 3)


def test_schema_requires_weights_at_least_one() -> None:
    """Схема и домен согласованы: вес задания и вес сектора - не меньше 1."""
    import json
    from pathlib import Path

    schema_path = (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "progression"
        / "progression_config.v1.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    quest = schema["properties"]["quests"]["properties"]["items"]["items"]["properties"]["weight"]
    sector = schema["properties"]["wheel"]["properties"]["sectors"]["items"]["properties"]["weight"]
    assert quest["minimum"] == 1
    assert sector["minimum"] == 1


def test_pool_smaller_than_count_is_allowed(make_config: Make) -> None:
    cfg = ProgressionConfig.parse(make_config())
    assert cfg.quests.weekly_count > len(cfg.quests.pool("weekly"))


def _dup_quest(c: dict[str, Any]) -> None:
    c["quests"]["items"][1]["key"] = c["quests"]["items"][0]["key"]


def _dup_badge(c: dict[str, Any]) -> None:
    c["badges"][1]["key"] = c["badges"][0]["key"]


def _dup_sector(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][1]["key"] = c["wheel"]["sectors"][0]["key"]


def _quest_metric(c: dict[str, Any]) -> None:
    c["quests"]["items"][0]["metric"] = "moon_phase"


def _badge_metric(c: dict[str, Any]) -> None:
    c["badges"][0]["metric"] = "moon_phase"


def _quest_period(c: dict[str, Any]) -> None:
    c["quests"]["items"][0]["period"] = "monthly"


def _quest_target_zero(c: dict[str, Any]) -> None:
    c["quests"]["items"][0]["target"] = 0


def _quest_domain(c: dict[str, Any]) -> None:
    c["quests"]["items"][0]["domain"] = "books"


def _one_sector(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"] = c["wheel"]["sectors"][:1]


def _boost_without_matches(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][6]["prize"]["matches"] = None


def _boost_multiplier_one(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][6]["prize"]["multiplier"] = 1


def _xp_zero_amount(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][0]["prize"]["amount"] = 0


def _none_with_amount(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][11]["prize"]["amount"] = 5


def _xp_with_multiplier(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][0]["prize"]["multiplier"] = 2


def _item_without_key(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][10]["prize"]["item_key"] = None


def _queue_zero(c: dict[str, Any]) -> None:
    c["wheel"]["max_boost_queue"] = 0


def _quest_weight_zero(c: dict[str, Any]) -> None:
    c["quests"]["items"][0]["weight"] = 0


def _sector_weight_zero(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][3]["weight"] = 0


def _unknown_kind(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][0]["prize"]["kind"] = "jackpot"


@pytest.mark.parametrize(
    "mutate",
    [
        _dup_quest,
        _dup_badge,
        _dup_sector,
        _quest_metric,
        _badge_metric,
        _quest_period,
        _quest_target_zero,
        _quest_domain,
        _one_sector,
        _boost_without_matches,
        _boost_multiplier_one,
        _xp_zero_amount,
        _none_with_amount,
        _xp_with_multiplier,
        _item_without_key,
        _queue_zero,
        _unknown_kind,
        _quest_weight_zero,
        _sector_weight_zero,
    ],
)
def test_invalid_rewards_config_is_rejected(
    make_config: Make, mutate: Callable[[dict[str, Any]], None]
) -> None:
    body = make_config()
    mutate(body)
    with pytest.raises(ConfigInvalidError) as exc:
        ProgressionConfig.parse(body)
    assert exc.value.code == "progression.config_invalid"
