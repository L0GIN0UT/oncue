"""Секции конфига 5b: ``shop``, ``promo``, ``spend`` (ADR 0043, 0045, 0048)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from progression_service.domain.config import ConfigInvalidError, ProgressionConfig

pytestmark = pytest.mark.unit

MakeConfig = Callable[..., dict[str, Any]]


def test_example_sections_are_parsed(make_config: MakeConfig) -> None:
    cfg = ProgressionConfig.parse(make_config())

    assert cfg.shop is not None
    assert [s.key for s in cfg.shop.slots] == ["avatar", "frame", "title", "background"]
    assert {r.key: r.duplicate_verses for r in cfg.shop.rarities} == {
        "common": 5,
        "rare": 15,
        "epic": 40,
    }
    assert cfg.shop.media.max_side_px == 512
    assert cfg.shop.custom_avatar_item_key == "custom_avatar"
    assert cfg.promo is not None
    assert (cfg.promo.code_length, cfg.promo.attempts_per_user) == (12, 10)
    assert cfg.spend is not None
    assert cfg.spend.bucket_order == ("earned", "paid")


def test_sections_are_optional_and_mean_empty_shop(make_config: MakeConfig) -> None:
    body = make_config(shop=..., promo=..., spend=...)

    cfg = ProgressionConfig.parse(body)

    assert cfg.shop is None
    assert cfg.promo is None
    assert cfg.spend is None


def test_shop_slot_and_rarity_lookup(make_config: MakeConfig) -> None:
    cfg = ProgressionConfig.parse(make_config())
    assert cfg.shop is not None

    assert cfg.shop.slot("frame") is not None
    assert cfg.shop.slot("nope") is None
    rarity = cfg.shop.rarity("rare")
    assert rarity is not None
    assert rarity.duplicate_verses == 15
    assert cfg.shop.rarity("nope") is None


def _duplicate_slot(body: dict[str, Any]) -> None:
    body["shop"]["slots"].append(dict(body["shop"]["slots"][0]))


def _duplicate_rarity(body: dict[str, Any]) -> None:
    body["shop"]["rarities"].append(dict(body["shop"]["rarities"][0]))


def _no_earned(body: dict[str, Any]) -> None:
    body["spend"]["bucket_order"] = ["paid"]


def _duplicate_bucket(body: dict[str, Any]) -> None:
    body["spend"]["bucket_order"] = ["earned", "earned"]


def _unknown_bucket(body: dict[str, Any]) -> None:
    body["spend"]["bucket_order"] = ["earned", "gold"]


def _empty_slots(body: dict[str, Any]) -> None:
    body["shop"]["slots"] = []


def _bad_slot_key(body: dict[str, Any]) -> None:
    body["shop"]["slots"][0]["key"] = "Bad Key"


def _bool_amount(body: dict[str, Any]) -> None:
    body["shop"]["rarities"][0]["duplicate_verses"] = True


def _short_code(body: dict[str, Any]) -> None:
    body["promo"]["code_length"] = 7


def _zero_window(body: dict[str, Any]) -> None:
    body["promo"]["window_s"] = 0


def _bad_avatar_key(body: dict[str, Any]) -> None:
    body["shop"]["custom_avatar_item_key"] = "Bad Key"


def _one_frame(body: dict[str, Any]) -> None:
    body["shop"]["media"]["animated_max_frames"] = 1


def _missing_media(body: dict[str, Any]) -> None:
    del body["shop"]["media"]


@pytest.mark.parametrize(
    "mutate",
    [
        _duplicate_slot,
        _duplicate_rarity,
        _no_earned,
        _duplicate_bucket,
        _unknown_bucket,
        _empty_slots,
        _bad_slot_key,
        _bool_amount,
        _short_code,
        _zero_window,
        _bad_avatar_key,
        _one_frame,
        _missing_media,
    ],
)
def test_invalid_sections_are_rejected(
    make_config: MakeConfig, mutate: Callable[[dict[str, Any]], None]
) -> None:
    body = make_config()
    mutate(body)
    with pytest.raises(ConfigInvalidError):
        ProgressionConfig.parse(body)


def test_custom_avatar_key_may_be_null(make_config: MakeConfig) -> None:
    body = make_config()
    body["shop"]["custom_avatar_item_key"] = None

    cfg = ProgressionConfig.parse(body)

    assert cfg.shop is not None
    assert cfg.shop.custom_avatar_item_key is None
