"""UGC-текст (ADR 0030): golden-таблицы нормализации, denylist с похожими символами, ссылки."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_text.ugc import (
    UGC_DENIED,
    UGC_EMAIL,
    UGC_EMPTY,
    UGC_LINK,
    UGC_MENTION,
    UGC_TOO_LONG,
    UgcPolicy,
    UgcViolation,
    normalize_ugc,
    skeleton,
)

pytestmark = pytest.mark.unit

POLICY = UgcPolicy.from_file()

# Вход → нормализованная строка. Таблица — контракт: collections и library сохраняют
# ровно этот результат, фронтенд показывает его же.
ENGLAND = "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
NORMALIZE_GOLDEN: list[tuple[str, str]] = [
    ("  Кино   ·  кадры  ", "Кино · кадры"),
    ("Ужасы\t80-х", "Ужасы 80-х"),
    ("Ужасы\u00a080-х", "Ужасы 80-х"),  # неразрывный пробел
    ("Ужасы\u3000\u3000«80-х»", "Ужасы «80-х»"),  # идеографический пробел (NFKC)
    # bidi: переопределение направления и изоляты удаляются
    ("abc\u202edcba", "abcdcba"),
    ("\u2067Кино\u2069", "Кино"),
    ("\u200fRTL\u200e", "RTL"),
    # zero-width и прочие невидимые
    ("ху\u200bй", "хуй"),
    ("Ки\u200cно\u2060\ufeff", "Кино"),
    ("пере\u00adнос", "перенос"),  # мягкий перенос
    ("a\u0000b\u0007c", "abc"),  # управляющие
    # NFKC: полноширинные, «математические» буквы, лигатуры, дроби
    ("ＫＩＮＯ\u3000２０２４", "KINO 2024"),
    ("\U0001d40a\U0001d422\U0001d427\U0001d428", "Kino"),
    ("ﬁlm", "film"),
    ("½", "1⁄2"),
    # ZWJ остаётся только внутри эмодзи, флаг-тег целиком
    ("Семья 👨\u200d👩\u200d👧", "Семья 👨\u200d👩\u200d👧"),
    ("🏳️\u200d🌈", "🏳️\u200d🌈"),
    (ENGLAND, ENGLAND),  # флаг-тег
    ("ab\U000e0067c", "abc"),  # тег вне флага удаляется
    ("👍🏽 ❤️ 🇷🇺", "👍🏽 ❤️ 🇷🇺"),
    # переводы строк — пробелы (однострочный режим)
    ("строка\nдругая\r\nтретья\u2028", "строка другая третья"),
]


@pytest.mark.parametrize(("raw", "expected"), NORMALIZE_GOLDEN)
def test_normalize_golden(raw: str, expected: str) -> None:
    assert normalize_ugc(raw) == expected


def test_normalize_multiline_keeps_paragraphs() -> None:
    raw = "  Первый   абзац \n\n\n\n Второй\u200b абзац\r\nтретья строка  "
    assert normalize_ugc(raw, multiline=True) == "Первый абзац\n\nВторой абзац\nтретья строка"


@given(st.text(max_size=80))
def test_normalize_is_idempotent(text: str) -> None:
    once = normalize_ugc(text)
    assert normalize_ugc(once) == once
    assert once == once.strip()
    assert "  " not in once


# Похожие символы, регистр, диакритика, повторы и знаки внутри слова — один скелет.
SKELETON_GOLDEN: list[tuple[str, str]] = [
    ("хуй", "хуи"),
    ("XУЙ", "хуи"),  # латинская X
    ("xyй", "хуи"),  # латинские x и y
    ("Х.У.Й", "хуи"),
    ("ххууууй", "хуи"),
    ("ΧУЙ", "хуи"),  # греческая Χ
    ("FUCK", skeleton("fuck")),
    ("f.u.c.k", skeleton("fuck")),
    ("fück", skeleton("fuck")),
    ("ёлка", "елка"),
    ("П0ЕЗД", "поезд"),  # ноль вместо «О»
]


@pytest.mark.parametrize(("word", "expected"), SKELETON_GOLDEN)
def test_skeleton_golden(word: str, expected: str) -> None:
    assert skeleton(word) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Лучшие хуй фильмы",
        "XУЙНЯ какая-то",  # основа «хуй*», латинская X
        "ху\u200bйня",  # zero-width внутри слова
        "Х.у.Й",
        "ПИЗДЕЦ",
        "спиздили",  # часть «*пизд*»
        "Fuck this",
        "ＦＵＣＫ",  # полноширинные
        "fucking movies",
        "motherfucker",
        "бля",
        "блять",
        "Shit",
    ],
)
def test_denylist_catches_variants(text: str) -> None:
    with pytest.raises(UgcViolation) as exc:
        POLICY.check(text, max_len=80)
    assert exc.value.code == UGC_DENIED
    assert exc.value.details == {}  # найденное слово наружу не уходит


@pytest.mark.parametrize(
    "text",
    [
        "Застрахуй машину",  # «хуй» внутри слова, а не основа
        "Страхуется каждый",
        "Бляха-муха",
        "Shiitake и другие грибы",
        "Мандарин и манго",
        "Учёба на дому",
        "Ребус для эрудитов",
        "Кино · кадры",
        "Ужасы 80-х",
    ],
)
def test_denylist_has_no_false_positives(text: str) -> None:
    assert POLICY.check(text, max_len=80) == normalize_ugc(text)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Смотри https://example.org", UGC_LINK),
        ("hxxp://evil", UGC_LINK),
        ("www.kino", UGC_LINK),
        ("Канал t.me", UGC_LINK),
        ("лучшее на kino.ru", UGC_LINK),
        ("ЛУЧШЕЕ.РФ", UGC_LINK),
        ("kino．ru", UGC_LINK),  # полноширинная точка → NFKC
        ("пишите me@example.com", UGC_EMAIL),
        ("автор @nick", UGC_MENTION),
        ("автор ＠nick", UGC_MENTION),  # полноширинное @
    ],
)
def test_title_forbids_links_mentions_emails(text: str, code: str) -> None:
    with pytest.raises(UgcViolation) as exc:
        POLICY.check(text, max_len=80, forbid_links=True)
    assert exc.value.code == code


@pytest.mark.parametrize("text", ["Mr.Robot и все-все", "Фильмы 2000-х", "Т.е. лучшее", "Ч/Б кино"])
def test_title_allows_ordinary_punctuation(text: str) -> None:
    assert POLICY.check(text, max_len=80, forbid_links=True) == text


def test_description_may_contain_links_but_not_denylist() -> None:
    text = "Подборка с сайта kino.ru\n\nпро @героев"
    assert POLICY.check(text, max_len=500, multiline=True) == text
    with pytest.raises(UgcViolation):
        POLICY.check("описание\nс хуйнёй", max_len=500, multiline=True)


def test_length_is_counted_after_normalization() -> None:
    padded = "а" + "\u200b" * 100 + "б" + " " * 50
    assert POLICY.check(padded, max_len=2) == "аб"
    with pytest.raises(UgcViolation) as exc:
        POLICY.check("абв", max_len=2)
    assert (exc.value.code, exc.value.details) == (UGC_TOO_LONG, {"max_len": 2, "length": 3})


@pytest.mark.parametrize("text", ["", "   ", "\u200b\u202e", "\u0000\t\n"])
def test_empty_after_normalization(text: str) -> None:
    with pytest.raises(UgcViolation) as exc:
        POLICY.check(text, max_len=80)
    assert exc.value.code == UGC_EMPTY


def test_policy_from_entries_and_file(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    path = tmp_path / "deny.txt"
    path.write_text("# свой список\nспойлер\nспойл*\n*ответ*\n\n", encoding="utf-8")
    policy = UgcPolicy.from_file(path)
    assert policy == UgcPolicy.from_entries(["спойлер", "спойл*", "*ответ*"])
    for text in ("СПОЙЛЕР", "спойлеры", "заответить"):
        with pytest.raises(UgcViolation):
            policy.check(text, max_len=80)
    # Свой файл заменяет встроенный список целиком.
    assert policy.check("бля", max_len=80) == "бля"
