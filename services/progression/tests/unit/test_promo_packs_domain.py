"""Домен промокодов и паков: нормализация, генерация, погашение, потолок эмиссии (ADR 0048)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import packs, promo
from progression_service.domain.promo import PromoCode, RewardKind

from gv_core.ids import uuid7
from gv_core.random import SeededRandom

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def make_code(**patch: Any) -> PromoCode:
    base = PromoCode(
        code_id=uuid7(),
        batch_id=uuid7(),
        reward_kind=RewardKind.VERSES,
        verses_amount=50,
        item_id=None,
        max_uses=2,
        activations=0,
        per_user_once=True,
        expires_at=NOW + timedelta(days=1),
        revoked=False,
    )
    return replace(base, **patch)


# ------------------------------------------------------------------ нормализация
def test_normalization_ignores_case_spaces_and_dashes() -> None:
    assert promo.normalize_code("  abcd-efgh jklm ") == "ABCDEFGHJKLM"
    assert promo.normalize_code("ABCD–EFGH") == "ABCDEFGH"  # типографское тире
    assert promo.normalize_code("") == ""


@given(st.text(max_size=40))
def test_normalization_is_idempotent(raw: str) -> None:
    once = promo.normalize_code(raw)
    assert promo.normalize_code(once) == once


@given(st.text(max_size=40))
def test_normalized_code_has_only_latin_letters_and_digits(raw: str) -> None:
    assert promo.normalize_code(raw).isalnum() or promo.normalize_code(raw) == ""
    assert promo.normalize_code(raw).isascii()


# ------------------------------------------------------------------ генерация
def test_generated_codes_use_safe_alphabet_and_are_unique() -> None:
    codes = promo.generate_codes(SeededRandom(b"seed"), count=200, length=12)

    assert len(codes) == len(set(codes)) == 200
    for code in codes:
        assert len(code) == 12
        assert set(code) <= set(promo.ALPHABET)
    assert not set("01OIL") & set(promo.ALPHABET)  # похожие символы исключены


def test_generation_is_deterministic_by_seed() -> None:
    a = promo.generate_codes(SeededRandom(b"x"), count=5, length=10)
    b = promo.generate_codes(SeededRandom(b"x"), count=5, length=10)
    assert a == b


def test_display_format_groups_by_four_and_normalizes_back() -> None:
    assert promo.format_code("ABCDEFGHJKMN") == "ABCD-EFGH-JKMN"
    assert promo.normalize_code(promo.format_code("ABCDEFGHJKMN")) == "ABCDEFGHJKMN"


# ------------------------------------------------------------------ погашение
def test_fresh_code_is_redeemable() -> None:
    promo.check_redeemable(make_code(), NOW, batch_redeemed_by_user=False)


@pytest.mark.parametrize(
    "patch",
    [
        {"revoked": True},
        {"expires_at": NOW},
        {"expires_at": NOW - timedelta(seconds=1)},
        {"activations": 2},
        {"activations": 3},
    ],
)
def test_unusable_codes_look_the_same(patch: dict[str, Any]) -> None:
    with pytest.raises(promo.PromoInvalidError) as err:
        promo.check_redeemable(make_code(**patch), NOW, batch_redeemed_by_user=False)
    assert err.value.code == "progression.promo_invalid"
    assert err.value.message == promo.PromoInvalidError().message


def test_per_user_once_blocks_second_code_of_batch() -> None:
    with pytest.raises(promo.PromoInvalidError):
        promo.check_redeemable(make_code(), NOW, batch_redeemed_by_user=True)
    promo.check_redeemable(make_code(per_user_once=False), NOW, batch_redeemed_by_user=True)


def test_code_without_expiry_never_expires() -> None:
    promo.check_redeemable(
        make_code(expires_at=None), NOW + timedelta(days=9999), batch_redeemed_by_user=False
    )


def test_redemption_op_id() -> None:
    code, user = uuid7(), uuid7()
    assert promo.redemption_op_id(code, user) == f"promo:{code}:{user}"


# ------------------------------------------------------------------ эмиссия и награда
def test_batch_emission_is_count_times_uses_times_amount() -> None:
    assert promo.batch_emission(10, 3, 50) == 1500
    assert promo.batch_emission(10, 3, None) == 0


def test_emission_ceiling() -> None:
    promo.check_batch_emission(10, 3, 50, ceiling=1500)
    with pytest.raises(promo.PromoBatchTooLargeError) as err:
        promo.check_batch_emission(10, 3, 50, ceiling=1499)
    assert err.value.code == "progression.promo_batch_too_large"
    promo.check_batch_emission(1000, 1000, None, ceiling=1)  # предметы не эмитируют Версы


@given(
    count=st.integers(1, 1000),
    uses=st.integers(1, 1000),
    amount=st.integers(1, 10**6),
)
def test_emission_is_monotonic(count: int, uses: int, amount: int) -> None:
    base = promo.batch_emission(count, uses, amount)
    assert promo.batch_emission(count + 1, uses, amount) > base
    assert promo.batch_emission(count, uses + 1, amount) > base
    assert promo.batch_emission(count, uses, amount + 1) > base


def test_reward_shape_must_match_kind() -> None:
    promo.check_reward(RewardKind.VERSES, verses_amount=10, item_id=None)
    promo.check_reward(RewardKind.ITEM, verses_amount=None, item_id=uuid7())
    for kind, amount, item in [
        (RewardKind.VERSES, None, None),
        (RewardKind.VERSES, 10, uuid7()),
        (RewardKind.ITEM, None, None),
        (RewardKind.ITEM, 10, uuid7()),
    ]:
        with pytest.raises(promo.PromoRewardInvalidError):
            promo.check_reward(kind, verses_amount=amount, item_id=item)


# ------------------------------------------------------------------ паки
def test_pack_items_must_be_unique_and_non_empty() -> None:
    a, b = uuid7(), uuid7()
    packs.check_pack_items([a, b])
    with pytest.raises(packs.PackInvalidError):
        packs.check_pack_items([])
    with pytest.raises(packs.PackInvalidError):
        packs.check_pack_items([a, a])
