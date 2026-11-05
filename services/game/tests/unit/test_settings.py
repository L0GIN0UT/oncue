"""Настройки game: значения по умолчанию, видимые игроку."""

from __future__ import annotations

import pytest
from game_service.settings import GameSettings

pytestmark = pytest.mark.unit


def test_share_title_defaults_to_product_name() -> None:
    """Подпись в тексте «Поделиться» - название продукта (B035: раньше было «OnCue»)."""
    assert GameSettings().share_title == "Guessverse"
