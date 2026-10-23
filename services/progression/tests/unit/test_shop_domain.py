"""Домен магазина, инвентаря и оформления: правила предмета (ADR 0045)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import appearance, inventory, shop
from progression_service.domain.config import MediaLimits, ProgressionConfig, ShopConfig
from progression_service.domain.shop import Item, ItemState, MediaInfo

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
MakeConfig = Callable[..., dict[str, Any]]


def make_item(**patch: Any) -> Item:
    base = Item(
        id=uuid7(),
        key="frame_gold",
        slot="frame",
        rarity="rare",
        title="Золотая рамка",
        media_kind=shop.MediaKind.ANIMATED,
        asset_key="c/" + "a" * 32 + ".webp",
        poster_key="c/" + "b" * 32 + ".webp",
        level_required=None,
        price=30,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(base, **patch)


@pytest.fixture
def shop_cfg(make_config: MakeConfig) -> ShopConfig:
    cfg = ProgressionConfig.parse(make_config())
    assert cfg.shop is not None
    return cfg.shop


# ------------------------------------------------------------------ состояние витрины
def test_state_owned_wins() -> None:
    item = make_item(level_required=50, price=None)
    assert shop.item_state(item, level=1, owned=True) is ItemState.OWNED


def test_state_available_by_level_or_price() -> None:
    assert shop.item_state(make_item(price=None, level_required=3), level=3, owned=False) is (
        ItemState.AVAILABLE
    )
    # цена открывает покупку раньше уровня
    assert shop.item_state(make_item(price=10, level_required=50), level=1, owned=False) is (
        ItemState.AVAILABLE
    )


def test_state_locked_when_level_not_reached_and_not_for_sale() -> None:
    item = make_item(price=None, level_required=3)
    assert shop.item_state(item, level=2, owned=False) is ItemState.LOCKED


# ------------------------------------------------------------------ покупка
def test_purchase_checks() -> None:
    assert shop.check_purchasable(make_item(price=30), owned=False) == 30
    with pytest.raises(shop.ItemNotFoundError):
        shop.check_purchasable(make_item(active=False), owned=False)
    with pytest.raises(shop.AlreadyOwnedError):
        shop.check_purchasable(make_item(), owned=True)
    with pytest.raises(shop.ItemNotAvailableError):
        shop.check_purchasable(make_item(price=None, level_required=5), owned=False)


def test_funds_check_boundaries() -> None:
    shop.check_funds(30, 30)
    with pytest.raises(shop.InsufficientFundsError) as err:
        shop.check_funds(30, 29)
    assert err.value.code == "progression.insufficient_funds"


@given(price=st.integers(min_value=1, max_value=10**8), balance=st.integers(0, 10**8))
def test_funds_never_allow_negative_balance(price: int, balance: int) -> None:
    try:
        shop.check_funds(price, balance)
    except shop.InsufficientFundsError:
        assert balance < price
    else:
        assert balance - price >= 0


def test_purchase_op_id_is_per_user_and_item() -> None:
    user, item = uuid7(), uuid7()
    assert shop.purchase_op_id(user, item) == f"shop:{user}:{item}"


# ------------------------------------------------------------------ поля предмета
def test_item_fields_must_use_known_slot_and_rarity(shop_cfg: ShopConfig) -> None:
    shop.validate_item_fields(
        slot="frame", rarity="rare", level_required=None, price=10, shop_cfg=shop_cfg
    )
    with pytest.raises(shop.ItemSlotUnknownError):
        shop.validate_item_fields(
            slot="wings", rarity="rare", level_required=None, price=10, shop_cfg=shop_cfg
        )
    with pytest.raises(shop.ItemRarityUnknownError):
        shop.validate_item_fields(
            slot="frame", rarity="mythic", level_required=None, price=10, shop_cfg=shop_cfg
        )


def test_item_without_unlock_condition_is_forbidden(shop_cfg: ShopConfig) -> None:
    with pytest.raises(shop.ItemUnlockRequiredError):
        shop.validate_item_fields(
            slot="frame", rarity="rare", level_required=None, price=None, shop_cfg=shop_cfg
        )


def test_without_shop_section_every_slot_is_unknown() -> None:
    with pytest.raises(shop.ItemSlotUnknownError):
        shop.validate_item_fields(
            slot="frame", rarity="rare", level_required=None, price=1, shop_cfg=None
        )


def test_activation_needs_media() -> None:
    shop.check_activation(active=True, asset_key="c/x.webp")
    shop.check_activation(active=False, asset_key=None)
    with pytest.raises(shop.ItemMediaMissingError):
        shop.check_activation(active=True, asset_key=None)


def test_key_is_immutable() -> None:
    shop.check_key_unchanged("a", "a")
    with pytest.raises(shop.ItemKeyImmutableError):
        shop.check_key_unchanged("a", "b")


def test_custom_avatar_item_must_exist_in_known_slot(shop_cfg: ShopConfig) -> None:
    shop.validate_custom_avatar(shop_cfg, {"custom_avatar": "avatar"})
    with pytest.raises(shop.CustomAvatarItemError):
        shop.validate_custom_avatar(shop_cfg, {})
    with pytest.raises(shop.CustomAvatarItemError):
        shop.validate_custom_avatar(shop_cfg, {"custom_avatar": "wings"})
    shop.validate_custom_avatar(replace(shop_cfg, custom_avatar_item_key=None), {})


# ------------------------------------------------------------------ медиа
LIMITS = MediaLimits(
    static_max_bytes=1000,
    animated_max_bytes=5000,
    animated_max_frames=10,
    animated_max_duration_ms=2000,
    max_side_px=128,
)


def info(**patch: Any) -> MediaInfo:
    base = MediaInfo(format="webp", width=64, height=64, frames=4, duration_ms=800, size_bytes=900)
    return replace(base, **patch)


def test_valid_animated_and_static_media_pass() -> None:
    shop.check_media(info(), shop.MediaKind.ANIMATED, LIMITS)
    shop.check_media(info(frames=1, duration_ms=0), shop.MediaKind.STATIC, LIMITS)
    shop.check_media(info(format="png", frames=1, duration_ms=0), shop.MediaKind.STATIC, LIMITS)


@pytest.mark.parametrize(
    ("kind", "patch"),
    [
        (shop.MediaKind.ANIMATED, {"frames": 1}),  # анимация без анимации
        (shop.MediaKind.ANIMATED, {"format": "png"}),  # анимация только WebP
        (shop.MediaKind.ANIMATED, {"frames": 11}),
        (shop.MediaKind.ANIMATED, {"duration_ms": 2001}),
        (shop.MediaKind.ANIMATED, {"width": 129}),
        (shop.MediaKind.ANIMATED, {"height": 129}),
        (shop.MediaKind.STATIC, {"frames": 2}),  # статика не анимирована
        (shop.MediaKind.STATIC, {"format": "gif"}),
    ],
)
def test_invalid_media_is_rejected(kind: shop.MediaKind, patch: dict[str, Any]) -> None:
    with pytest.raises(shop.ItemMediaInvalidError) as err:
        shop.check_media(info(**patch), kind, LIMITS)
    assert err.value.code == "progression.item_media_invalid"


def test_oversized_media_is_too_large() -> None:
    with pytest.raises(shop.ItemMediaTooLargeError):
        shop.check_media(info(size_bytes=5001), shop.MediaKind.ANIMATED, LIMITS)
    with pytest.raises(shop.ItemMediaTooLargeError):
        shop.check_media(info(frames=1, size_bytes=1001), shop.MediaKind.STATIC, LIMITS)


def test_upload_limit_depends_on_kind() -> None:
    assert shop.max_upload_bytes(shop.MediaKind.STATIC, LIMITS) == 1000
    assert shop.max_upload_bytes(shop.MediaKind.ANIMATED, LIMITS) == 5000


def test_object_keys_are_random_hex_under_prefix() -> None:
    key = shop.object_key("0123456789abcdef0123456789abcdef", "webp")
    assert key == "c/0123456789abcdef0123456789abcdef.webp"
    with pytest.raises(ValueError, match="hex"):
        shop.object_key("not-hex", "webp")


# ------------------------------------------------------------------ инвентарь
def test_inventory_hides_expired_entries() -> None:
    def entry(expires: datetime | None) -> inventory.InventoryItem:
        return inventory.InventoryItem(
            user_id=uuid7(),
            item_id=uuid7(),
            source=inventory.Source.PURCHASE,
            acquired_at=NOW,
            expires_at=expires,
            convert_condition=None,
        )

    assert inventory.is_visible(entry(None), NOW)
    assert inventory.is_visible(entry(NOW + timedelta(seconds=1)), NOW)
    assert not inventory.is_visible(entry(NOW), NOW)
    assert not inventory.is_visible(entry(NOW - timedelta(days=1)), NOW)


def test_level_unlocks_pick_active_items_at_or_below_level_not_owned() -> None:
    low = make_item(key="low", level_required=2, price=None)
    high = make_item(key="high", level_required=9, price=None)
    paid = make_item(key="paid", level_required=None, price=5)
    owned = make_item(key="owned", level_required=1, price=None)
    off = make_item(key="off", level_required=1, price=None, active=False)

    result = inventory.level_unlocks([low, high, paid, owned, off], level=2, owned_ids={owned.id})

    assert [i.key for i in result] == ["low"]


# ------------------------------------------------------------------ оформление
def test_equip_rules() -> None:
    slots = ["avatar", "frame"]
    item = make_item(slot="frame")
    appearance.check_equip(slots, "frame", item, owned=True)
    appearance.check_equip(slots, "frame", None, owned=False)  # снять
    with pytest.raises(appearance.SlotNotFoundError):
        appearance.check_equip(slots, "wings", item, owned=True)
    with pytest.raises(appearance.ItemNotOwnedError):
        appearance.check_equip(slots, "frame", item, owned=False)
    with pytest.raises(appearance.ItemNotOwnedError):  # предмета нет вовсе - тот же ответ
        appearance.check_equip(slots, "frame", None, owned=True, item_id=uuid7())
    with pytest.raises(appearance.ItemSlotMismatchError):
        appearance.check_equip(slots, "avatar", item, owned=True)


def test_inactive_item_cannot_be_equipped_even_if_owned() -> None:
    with pytest.raises(appearance.ItemNotOwnedError):
        appearance.check_equip(["frame"], "frame", make_item(active=False), owned=True)


def test_equip_unknown_slot_is_checked_before_item() -> None:
    with pytest.raises(appearance.SlotNotFoundError):
        appearance.check_equip([], "frame", None, owned=False)


def test_user_ids_are_deduplicated_keeping_order() -> None:
    a, b = UUID(int=1), UUID(int=2)
    assert appearance.unique_user_ids([a, b, a, b, a]) == [a, b]


@given(st.lists(st.integers(min_value=0, max_value=5), max_size=12))
def test_unique_user_ids_is_idempotent_and_has_no_duplicates(raw: list[int]) -> None:
    ids = [UUID(int=n) for n in raw]
    once = appearance.unique_user_ids(ids)
    assert appearance.unique_user_ids(once) == once
    assert len(set(once)) == len(once)
