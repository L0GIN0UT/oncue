"""Нормализация названий: golden-таблица и свойства (идемпотентность)."""

from __future__ import annotations

import unicodedata

import pytest
from catalog_service.domain.normalization import NormalizerConfig, TitleNormalizer
from hypothesis import given
from hypothesis import strategies as st

pytestmark = pytest.mark.unit

N = TitleNormalizer()

# Вход → ожидаемая нормализованная форма. Таблица — контракт поведения: game/match в
# этапе 2 сверяют ответы по этой же форме, поэтому правки здесь — осознанные.
GOLDEN: list[tuple[str, str]] = [
    ("Интерстеллар", "интерстеллар"),
    ("  ИНТЕРСТЕЛЛАР  ", "интерстеллар"),
    ("Ёжик в тумане", "ежик в тумане"),
    ("ЁЖИК, в тумане!", "ежик в тумане"),
    ("Брат 2", "брат 2"),
    ("Москва слезам не верит", "москва слезам не верит"),
    ("Иван Васильевич меняет профессию", "иван васильевич меняет профессию"),
    ("Мой-мой: йод", "мой мой йод"),  # «й» не теряет бреве
    ("The Beatles", "beatles"),
    ("The The", "the"),  # не снимаем единственное слово
    ("A Beautiful Mind", "beautiful mind"),
    ("the a team", "team"),  # снимаются все ведущие артикли (идемпотентность)
    ("Amélie", "amelie"),
    ("Léon: The Professional", "leon the professional"),
    ("Straße", "strasse"),
    ("ＷＡＬＬ·Ｅ", "wall e"),  # полноширинные символы (NFKC)
    ("Ｒｏｃｋｙ", "rocky"),
    ("ﬁght club", "fight club"),  # лигатура
    ("Pulp   Fiction", "pulp fiction"),
    ("Kill Bill: Vol. 1", "kill bill vol 1"),
    ("Don't Stop Me Now", "don t stop me now"),
    ("AC/DC — Back in Black", "ac dc back in black"),
    ("Rock & Roll", "rock roll"),
    ("«Кино» — Группа крови", "кино группа крови"),
    ("Smells Like Teen Spirit!!!", "smells like teen spirit"),
    ("...", ""),
    ("", ""),
    ("Tab\tand\nnewline", "tab and newline"),
    ("Nº 5", "no 5"),
]

TRANSLIT_GOLDEN: list[tuple[str, str | None]] = [
    ("интерстеллар", "interstellar"),
    ("ежик в тумане", "ezhik v tumane"),
    ("щука", "shchuka"),
    ("брат 2", "brat 2"),
    ("interstellar", "интерстеллар"),
    ("shchuka", "щука"),
    ("matrix", "матрикс"),
    ("ivan vasilevich", "иван василевич"),
    ("кино rock", None),  # смешанный алфавит — нет однозначного варианта
    ("2001", None),
    ("", None),
]


@pytest.mark.parametrize(("raw", "expected"), GOLDEN)
def test_normalize_golden(raw: str, expected: str) -> None:
    assert N.normalize(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), TRANSLIT_GOLDEN)
def test_translit_golden(raw: str, expected: str | None) -> None:
    assert N.translit(raw) == expected


def test_articles_come_from_config() -> None:
    custom = TitleNormalizer(NormalizerConfig(articles=("der", "die", "das")))
    assert custom.normalize("Das Boot") == "boot"
    assert custom.normalize("The Matrix") == "the matrix"


@given(st.text(max_size=80))
def test_normalize_is_idempotent(text: str) -> None:
    once = N.normalize(text)
    assert N.normalize(once) == once


@given(st.text(max_size=80))
def test_normalize_has_no_edge_or_double_spaces(text: str) -> None:
    value = N.normalize(text)
    assert value == value.strip()
    assert "  " not in value
    # casefold некоторых составных символов (U+1F50 «ὐ») даёт разложенную форму —
    # нормализатор сознательно возвращает её в NFKC, поэтому сравниваем с ней.
    assert value == unicodedata.normalize("NFKC", value.casefold())
    assert "ё" not in value


@given(st.text(alphabet="абвгдежзийклмнопрстуфхцчшщъыьэюя ", max_size=40))
def test_translit_of_cyrillic_is_latin_and_normalized(text: str) -> None:
    normalized = N.normalize(text)
    result = N.translit(normalized)
    if result is not None:
        assert result == N.normalize(result)
        assert all(not ("а" <= ch <= "я") for ch in result)
