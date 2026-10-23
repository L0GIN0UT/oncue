"""Подписанные токены отписки: HMAC над (user_id, broadcast_id) и версия ключа."""

from __future__ import annotations

import base64

import pytest
from hypothesis import given
from hypothesis import strategies as st
from notifications_service.domain.tokens import UnsubscribeTokens

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

KEY = b"k" * 32


def tokens(version: int = 1, **extra: bytes) -> UnsubscribeTokens:
    keys = {version: KEY}
    keys.update({int(k.removeprefix("v")): v for k, v in extra.items()})
    return UnsubscribeTokens(active_version=version, keys=keys)


def test_issued_token_verifies_to_same_ids() -> None:
    user, broadcast = uuid7(), uuid7()
    token = tokens().issue(user, broadcast)
    assert tokens().verify(token) == (user, broadcast)


def test_token_is_long_enough_for_request_schema() -> None:
    assert 16 <= len(tokens().issue(uuid7(), uuid7())) <= 512


def test_tampered_user_id_is_rejected() -> None:
    """Подмена user_id при той же подписи: чужого отписать нельзя."""
    user, other, broadcast = uuid7(), uuid7(), uuid7()
    version, ids, mac = tokens().issue(user, broadcast).split(".")
    forged_ids = base64.urlsafe_b64encode(other.bytes + broadcast.bytes).decode().rstrip("=")
    assert tokens().verify(f"{version}.{forged_ids}.{mac}") is None
    assert ids != forged_ids


def test_token_signed_with_other_key_is_rejected() -> None:
    other = UnsubscribeTokens(active_version=1, keys={1: b"x" * 32})
    assert tokens().verify(other.issue(uuid7(), uuid7())) is None


@pytest.mark.parametrize(
    "garbage", ["", "x", "1.2.3", "1..", "...", "a.b.c", "1." + "A" * 43 + "." + "B" * 43, "é" * 40]
)
def test_garbage_is_rejected_without_exception(garbage: str) -> None:
    assert tokens().verify(garbage) is None


@given(st.text(max_size=600))
def test_arbitrary_text_never_raises(text: str) -> None:
    assert tokens().verify(text) is None


def test_previous_key_still_verifies_after_rotation() -> None:
    old = UnsubscribeTokens(active_version=1, keys={1: KEY})
    token = old.issue(uuid7(), uuid7())
    rotated = UnsubscribeTokens(active_version=2, keys={1: KEY, 2: b"n" * 32})
    assert rotated.verify(token) is not None
    assert rotated.issue(uuid7(), uuid7()).startswith("2.")


def test_removed_key_version_is_rejected() -> None:
    old = UnsubscribeTokens(active_version=1, keys={1: KEY})
    token = old.issue(uuid7(), uuid7())
    rotated = UnsubscribeTokens(active_version=2, keys={2: b"n" * 32})
    assert rotated.verify(token) is None


def test_active_version_must_have_a_key() -> None:
    with pytest.raises(ValueError, match="активн"):
        UnsubscribeTokens(active_version=3, keys={1: KEY})


def test_short_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="короче"):
        UnsubscribeTokens(active_version=1, keys={1: b"short"})
