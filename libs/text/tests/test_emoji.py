"""Графемы и строгие эмодзи: golden-векторы (общие с catalog и media-pipeline) и таблица RGI."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_text.emoji import graphemes, is_strict_emoji, rgi_emoji

pytestmark = pytest.mark.unit


# Те же векторы, что у catalog (tests/unit/test_clue_texts.py) и media-pipeline
# (tests/unit/test_stage3a_generators.py): алгоритм должен совпадать побайтно.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("🎬🚀", ["🎬", "🚀"]),
        ("👨\u200d👩\u200d👧", ["👨\u200d👩\u200d👧"]),  # ZWJ-последовательность
        ("👍🏽❤️", ["👍🏽", "❤️"]),  # тон кожи, вариационный селектор
        ("🇷🇺🇺🇸", ["🇷🇺", "🇺🇸"]),  # флаги — пары индикаторов
        ("1️⃣й", ["1️⃣", "й"]),  # keycap; «й» из двух кодовых точек
        ("", []),
    ],
)
def test_graphemes_golden(text: str, expected: list[str]) -> None:
    assert graphemes(text) == expected


@given(st.text(max_size=40))
def test_graphemes_partition_text(text: str) -> None:
    parts = graphemes(text)
    assert "".join(parts) == text
    assert all(parts)


def test_rgi_table_is_unicode_16() -> None:
    """Таблица — RGI Unicode Emoji 16.0: 3790 последовательностей, есть эмодзи 16.0."""
    table = rgi_emoji()
    assert len(table) == 3790
    assert "\U0001fae9" in table  # 🫩 лицо с мешками под глазами (Emoji 16.0)
    assert "\U0001f1e6\U0001f1f6" in table  # 🇦🇶


@pytest.mark.parametrize(
    "text",
    [
        "🎬",
        "🎬🚀🍿",
        "❤️",  # Basic_Emoji с FE0F
        "👍🏽",  # тон кожи
        "👩🏿\u200d🚀",  # тон кожи + ZWJ
        "👨\u200d👩\u200d👧\u200d👦",  # семья через ZWJ
        "🧑\u200d🤝\u200d🧑",
        "🏳️\u200d🌈",
        "🇷🇺🇯🇵",  # флаги
        "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",  # флаг-тег (Шотландия)
        "#️⃣1️⃣",  # keycap
        "🫩",  # Emoji 16.0 (в unicodedata Python 3.13 не назначен)
    ],
)
def test_strict_emoji_accepts_rgi(text: str) -> None:
    assert is_strict_emoji(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "кино",
        "🎬 🚀",  # пробел
        "🎬a",
        "1",  # цифра без keycap
        "❤",  # без FE0F — текстовое представление, не RGI
        "🇷",  # непарный региональный индикатор
        "🇷🇺🇺",  # флаг и лишний индикатор
        "👍\u200d",  # висячий ZWJ
        "\u200d",
        "🎬\u202e",  # bidi-переопределение
        "🎬\u200b🚀",  # zero-width space
        "🇿🇿",  # пара индикаторов, но не флаг
        "©",  # без FE0F
    ],
)
def test_strict_emoji_rejects(text: str) -> None:
    assert not is_strict_emoji(text)


def test_strict_emoji_max_graphemes() -> None:
    assert is_strict_emoji("👨\u200d👩\u200d👧" * 12, max_graphemes=12)
    assert not is_strict_emoji("🚀" * 13, max_graphemes=12)
