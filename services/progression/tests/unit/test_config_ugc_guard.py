"""Секция конфига ``ugc_guard``: значения по умолчанию, границы, неизвестные ключи."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from progression_service.domain.config import ConfigInvalidError, ProgressionConfig, UgcGuardConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def parse(make_config: Callable[..., dict[str, Any]]) -> Callable[..., UgcGuardConfig]:
    def run(**patch: Any) -> UgcGuardConfig:
        return ProgressionConfig.parse(make_config(**patch)).ugc_guard

    return run


def test_absent_section_means_protection_on(parse: Callable[..., UgcGuardConfig]) -> None:
    cfg = parse(ugc_guard=...)
    assert cfg == UgcGuardConfig(
        own_collection_rewards=False, repeat_window_hours=24, repeat_scope="ugc"
    )
    assert cfg.ugc_daily_xp_cap is None


def test_values_are_read_from_config(parse: Callable[..., UgcGuardConfig]) -> None:
    cfg = parse(
        ugc_guard={
            "own_collection_rewards": True,
            "repeat_window_hours": 0,
            "repeat_scope": "all",
            "ugc_daily_xp_cap": 250,
        }
    )
    assert (cfg.own_collection_rewards, cfg.repeat_window_hours) == (True, 0)
    assert (cfg.repeat_scope, cfg.ugc_daily_xp_cap) == ("all", 250)


@pytest.mark.parametrize(
    "bad",
    [
        {"own_collection_rewards": "no"},
        {"repeat_window_hours": -1},
        {"repeat_window_hours": 87_601},
        {"repeat_window_hours": 1.5},
        {"repeat_scope": "none"},
        {"ugc_daily_xp_cap": -5},
        {"unknown_key": 1},
    ],
)
def test_invalid_values_are_rejected(
    parse: Callable[..., UgcGuardConfig], bad: dict[str, Any]
) -> None:
    with pytest.raises(ConfigInvalidError):
        parse(ugc_guard=bad)
