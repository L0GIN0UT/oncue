"""Домен: разбор и семантическая проверка конфига progression (``progression.config_invalid``)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from progression_service.domain.config import ConfigInvalidError, ProgressionConfig, band_amount

pytestmark = pytest.mark.unit


def test_example_config_is_parsed(make_config: Callable[..., dict[str, Any]]) -> None:
    cfg = ProgressionConfig.parse(make_config())
    assert cfg.levels.max_level == 100
    assert cfg.xp.party.place_bonus == (80, 50, 30)
    assert cfg.verses.weekly_top[-1].last == 100
    assert cfg.verses.season[-1].last == 1000
    assert cfg.tz.key == "Europe/Moscow"


def test_band_amount_follows_grid(make_config: Callable[..., dict[str, Any]]) -> None:
    cfg = ProgressionConfig.parse(make_config())
    grid = cfg.verses.season
    assert [band_amount(grid, p) for p in (1, 2, 3, 4, 10, 11, 100, 101, 1000, 1001)] == [
        2000,
        1200,
        800,
        400,
        400,
        150,
        150,
        50,
        50,
        0,
    ]


def _gap(c: dict[str, Any]) -> None:
    c["verses"]["weekly_top"][1]["from"] = 3
    c["verses"]["weekly_top"][1]["to"] = 3


def _overlap(c: dict[str, Any]) -> None:
    c["verses"]["season"][1]["from"] = 1


def _from_after_to(c: dict[str, Any]) -> None:
    c["verses"]["season"][3]["from"] = 10
    c["verses"]["season"][3]["to"] = 4


def _grid_not_from_one(c: dict[str, Any]) -> None:
    c["verses"]["weekly_top"][0]["from"] = 2


def _max_level_zero(c: dict[str, Any]) -> None:
    c["levels"]["max_level"] = 0


def _negative_step(c: dict[str, Any]) -> None:
    c["levels"]["step"] = -1


def _zero_weight(c: dict[str, Any]) -> None:
    c["wheel"]["sectors"][0]["weight"] = 0


def _fractional_sum(c: dict[str, Any]) -> None:
    c["xp"]["duel"]["win_bonus"] = 1.5


def _negative_sum(c: dict[str, Any]) -> None:
    c["verses"]["daily"] = -1


def _bool_sum(c: dict[str, Any]) -> None:
    c["verses"]["solo_round"] = True


def _bad_timezone(c: dict[str, Any]) -> None:
    c["timezone"] = "Mars/Olympus"


def _ratio_above_one(c: dict[str, Any]) -> None:
    c["min_rounds_ratio"] = 1.5


def _zero_divisor(c: dict[str, Any]) -> None:
    c["xp"]["solo_round"]["score_divisor"] = 0


def _missing_block(c: dict[str, Any]) -> None:
    del c["xp"]


@pytest.mark.parametrize(
    "mutate",
    [
        _gap,
        _overlap,
        _from_after_to,
        _grid_not_from_one,
        _max_level_zero,
        _negative_step,
        _zero_weight,
        _fractional_sum,
        _negative_sum,
        _bool_sum,
        _bad_timezone,
        _ratio_above_one,
        _zero_divisor,
        _missing_block,
    ],
)
def test_invalid_config_is_rejected_with_code(
    make_config: Callable[..., dict[str, Any]], mutate: Callable[[dict[str, Any]], None]
) -> None:
    body = copy.deepcopy(make_config())
    mutate(body)
    with pytest.raises(ConfigInvalidError) as exc:
        ProgressionConfig.parse(body)
    assert exc.value.code == "progression.config_invalid"
