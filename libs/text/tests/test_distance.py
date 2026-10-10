"""Расстояние Дамерау-Левенштейна: эталонные значения, ранний выход, свойства метрики."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_text import damerau_levenshtein

pytestmark = pytest.mark.unit

GOLDEN: list[tuple[str, str, int]] = [
    ("", "", 0),
    ("abc", "", 3),
    ("", "abc", 3),
    ("интерстеллар", "интерстеллар", 0),
    ("интерстелар", "интерстеллар", 1),  # пропуск
    ("интерстеллра", "интерстеллар", 1),  # перестановка соседних
    ("матрица", "матрицв", 1),  # замена
    ("kitten", "sitting", 3),
    ("ca", "abc", 2),  # полная версия: перестановка с вставкой (OSA дала бы 3)
    ("ab", "ba", 1),
    ("abcdef", "badcfe", 3),
]

short = st.text(alphabet="abcд", max_size=7)


@pytest.mark.parametrize(("a", "b", "expected"), GOLDEN)
def test_golden(a: str, b: str, expected: int) -> None:
    assert damerau_levenshtein(a, b) == expected
    assert damerau_levenshtein(b, a) == expected


def test_early_exit_caps_result() -> None:
    assert damerau_levenshtein("abc", "abcdefgh", max_distance=2) == 3  # по длине
    assert damerau_levenshtein("kitten", "sitting", max_distance=1) == 2
    assert damerau_levenshtein("kitten", "sitting", max_distance=3) == 3


def test_negative_threshold_is_rejected() -> None:
    with pytest.raises(ValueError, match="отрицательн"):
        damerau_levenshtein("a", "b", max_distance=-1)


@given(short, short)
def test_symmetry_and_identity(a: str, b: str) -> None:
    d = damerau_levenshtein(a, b)
    assert d == damerau_levenshtein(b, a)
    assert (d == 0) == (a == b)
    assert abs(len(a) - len(b)) <= d <= max(len(a), len(b))


@given(short, short, short)
def test_triangle_inequality(a: str, b: str, c: str) -> None:
    assert damerau_levenshtein(a, c) <= damerau_levenshtein(a, b) + damerau_levenshtein(b, c)


@given(short, short, st.integers(min_value=0, max_value=4))
def test_threshold_is_consistent_with_full_distance(a: str, b: str, k: int) -> None:
    full = damerau_levenshtein(a, b)
    assert damerau_levenshtein(a, b, max_distance=k) == min(full, k + 1)
