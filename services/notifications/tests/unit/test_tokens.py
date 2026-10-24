"""Непрозрачные токены отписки: случайность, хеш, отсутствие идентификаторов."""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st
from notifications_service.domain.tokens import hash_token, new_token

pytestmark = pytest.mark.unit


def test_token_has_256_bits_of_urlsafe_entropy() -> None:
    token = new_token()
    assert len(token) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)


def test_tokens_are_unique() -> None:
    assert len({new_token() for _ in range(500)}) == 500


def test_hash_is_sha256_hex_and_deterministic() -> None:
    token = new_token()
    assert hash_token(token) == hash_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_token(token))
    assert token not in hash_token(token)
    assert hash_token(token) != hash_token(new_token())


@given(st.text(max_size=600))
def test_arbitrary_text_hashes_without_exception(text: str) -> None:
    assert len(hash_token(text)) == 64
