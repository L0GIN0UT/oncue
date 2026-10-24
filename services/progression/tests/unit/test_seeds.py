"""Сид конфига (`seeds/progression/config.yaml`): схема, домен, пулы заданий, колесо."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from PIL import Image
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


ITEMS_DIR = ROOT / "seeds" / "progression" / "items"
ITEM_LIMIT_BYTES = 50_000


@pytest.fixture(scope="module")
def items_spec() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((ITEMS_DIR / "items.yaml").read_text("utf-8"))
    return data


def test_wheel_has_positive_weights_and_item_prizes_from_seed_items(
    raw: dict[str, Any], items_spec: dict[str, Any]
) -> None:
    sectors = raw["wheel"]["sectors"]

    assert sum(s["weight"] for s in sectors) > 0
    assert {s["prize"]["kind"] for s in sectors} == {"xp", "verses", "boost", "item", "none"}
    prize_items = [s["prize"]["item_key"] for s in sectors if s["prize"]["kind"] == "item"]
    assert 1 <= len(prize_items) <= 2
    assert set(prize_items) <= {i["key"] for i in items_spec["items"]}
    # Предмет-приз редкий: вес сектора маленький.
    assert all(s["weight"] <= 2 for s in sectors if s["prize"]["kind"] == "item")


def test_shop_promo_spend_owner_values(raw: dict[str, Any]) -> None:
    shop = raw["shop"]

    assert [s["key"] for s in shop["slots"]] == ["avatar", "frame", "title", "background"]
    assert {r["key"]: r["duplicate_verses"] for r in shop["rarities"]} == {
        "common": 5,
        "rare": 15,
        "epic": 40,
    }
    media = shop["media"]
    assert media["static_max_bytes"] <= ITEM_LIMIT_BYTES
    assert media["animated_max_bytes"] <= ITEM_LIMIT_BYTES
    promo = raw["promo"]
    assert (promo["attempts_per_user"], promo["attempts_per_ip"]) == (10, 30)
    assert (promo["window_s"], promo["code_length"]) == (3600, 12)
    assert (
        promo["max_batch_verses"]
        <= promo["max_daily_verses_per_actor"]
        <= promo["max_daily_verses_total"]
    )
    assert raw["spend"]["bucket_order"] == ["earned"]


def test_bootstrap_config_has_no_custom_avatar_key(raw: dict[str, Any]) -> None:
    """v1 публикует bootstrap до появления предметов: ключ ставит `gv dev seed` следующей версией.

    Иначе публикация упала бы с 422 `progression.config_invalid` (предмета ещё нет).
    """
    assert raw["shop"]["custom_avatar_item_key"] is None


def test_items_match_config_and_media_limits(
    raw: dict[str, Any], items_spec: dict[str, Any]
) -> None:
    items = items_spec["items"]
    slots = {s["key"] for s in raw["shop"]["slots"]}
    rarities = {r["key"] for r in raw["shop"]["rarities"]}
    limits = raw["shop"]["media"]
    keys = [i["key"] for i in items]

    assert len(keys) == len(set(keys))
    assert 12 <= len(items) <= 15
    assert {i["slot"] for i in items} == slots
    assert all(i["rarity"] in rarities for i in items)
    assert all(i["level_required"] or i["price"] for i in items)
    for slot in ("frame", "background"):
        assert any(i["slot"] == slot and i["media_kind"] == "animated" for i in items)
    avatar = next(i for i in items if i["key"] == items_spec["custom_avatar_item_key"])
    assert avatar["price"] and avatar["slot"] in slots and avatar["active"]

    for item in items:
        path = ITEMS_DIR / "images" / f"{item['key']}.webp"
        assert path.is_file(), path
        assert path.stat().st_size <= ITEM_LIMIT_BYTES
        limit = limits[
            "animated_max_bytes" if item["media_kind"] == "animated" else "static_max_bytes"
        ]
        assert path.stat().st_size <= limit
        with Image.open(path) as image:
            assert image.format == "WEBP"
            assert max(image.size) <= limits["max_side_px"]
            frames = getattr(image, "n_frames", 1)
            if item["media_kind"] == "animated":
                assert 2 <= frames <= limits["animated_max_frames"]
                total = 0
                for index in range(frames):
                    image.seek(index)
                    total += int(image.info.get("duration", 0))
                assert total <= limits["animated_max_duration_ms"]
            else:
                assert frames == 1


def test_images_are_distinct(items_spec: dict[str, Any]) -> None:
    digests = {
        (ITEMS_DIR / "images" / f"{i['key']}.webp").read_bytes() for i in items_spec["items"]
    }

    assert len(digests) == len(items_spec["items"])


def test_packs_reference_seed_items(items_spec: dict[str, Any]) -> None:
    packs = yaml.safe_load((ITEMS_DIR / "packs.yaml").read_text("utf-8"))["packs"]
    keys = {i["key"] for i in items_spec["items"]}

    assert len(packs) == 1
    assert packs[0]["active"] is False
    assert set(packs[0]["item_keys"]) <= keys
