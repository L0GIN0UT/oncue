"""Тексты нарушений публикации: каждый код политики имеет понятное сообщение."""

from __future__ import annotations

import pytest
from collections_service.domain import policy
from collections_service.domain.error_messages import error_message

pytestmark = pytest.mark.unit

_CODES = [
    value
    for name, value in vars(policy).items()
    if name.isupper() and isinstance(value, str) and value.startswith("collections.")
]


@pytest.mark.parametrize("code", _CODES)
def test_every_policy_code_has_specific_message(code: str) -> None:
    text = error_message(
        "items[2]", code, {"min": 5, "max": 9, "actual": 1, "reason": "ugc.denied_word"}
    )
    assert text
    assert text != "Сборник не проходит проверку"


def test_item_number_is_one_based() -> None:
    assert error_message("items[2]", "collections.item_duplicate", {}).startswith("Единица №3")


def test_unknown_code_falls_back() -> None:
    assert error_message("x", "collections.unknown", {}) == "Сборник не проходит проверку"
