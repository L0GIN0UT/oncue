"""Источник дистракторов ``similar`` (ADR 0040): фильтры близости, исключения, детерминизм."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from gv_core.random import SeededRandom
from gv_game_kernel import PluginRegistry, PoolTooSmallError
from gv_game_kernel.builtin import SimilarDistractorParams, SimilarDistractors, register_builtin
from gv_testkit.game import media_item

pytestmark = pytest.mark.unit

PLUGIN = SimilarDistractors()


def params(**over: Any) -> SimilarDistractorParams:
    body: dict[str, Any] = {
        "same_genre": False,
        "year_window": None,
        "popularity_ratio": None,
        "min_pool": 1,
    } | over
    return SimilarDistractorParams.model_validate(body)


def rng(seed: bytes = b"s") -> SeededRandom:
    return SeededRandom(seed)


def pool_of(n: int, **kw: Any) -> list[Any]:
    return [media_item(f"Item {i}", **kw) for i in range(n)]


def test_registered_as_builtin() -> None:
    registry = register_builtin(PluginRegistry())
    assert registry.distractor("similar") is not None
    assert registry.distractor("nope") is None


def test_params_follow_schema() -> None:
    with pytest.raises(ValidationError):
        params(min_pool=0)
    with pytest.raises(ValidationError):
        params(popularity_ratio=1.5)
    with pytest.raises(ValidationError):
        params(year_window=-1)
    with pytest.raises(ValidationError):
        SimilarDistractorParams.model_validate({"same_genre": True})


def test_excludes_correct_and_same_title() -> None:
    correct = media_item("Zed", aliases=("zed",))
    same_title = media_item("Zed (remaster)", title_aliases=("zed",))
    same_by_title = media_item("Zed")
    others = pool_of(4)
    picked = PLUGIN.pick(
        correct, [correct, same_title, same_by_title, *others], count=4, params=params(), rng=rng()
    )
    assert {i.id for i in picked} == {i.id for i in others}


def test_same_genre_filter() -> None:
    correct = media_item("C", genres=("rock", "pop"))
    near = pool_of(3, genres=("pop",))
    far = pool_of(3, genres=("jazz",))
    picked = PLUGIN.pick(correct, [*near, *far], count=3, params=params(same_genre=True), rng=rng())
    assert {i.id for i in picked} == {i.id for i in near}


def test_year_window_filter() -> None:
    correct = media_item("C", year=2000)
    near = [media_item(f"n{y}", year=y) for y in (1995, 2005, 2000)]
    far = [media_item("far", year=2020), media_item("none", year=None)]
    picked = PLUGIN.pick(correct, [*near, *far], count=3, params=params(year_window=5), rng=rng())
    assert {i.id for i in picked} == {i.id for i in near}


def test_popularity_ratio_filter() -> None:
    correct = media_item("C", popularity=0.8)
    near = [media_item("a", popularity=0.5), media_item("b", popularity=0.8)]
    far = [media_item("c", popularity=0.1), media_item("d", popularity=None)]
    picked = PLUGIN.pick(
        correct, [*near, *far], count=2, params=params(popularity_ratio=0.5), rng=rng()
    )
    assert {i.id for i in picked} == {i.id for i in near}


def test_too_few_candidates_raises_with_details() -> None:
    correct = media_item("C")
    with pytest.raises(PoolTooSmallError) as exc:
        PLUGIN.pick(correct, [correct, *pool_of(2)], count=3, params=params(), rng=rng())
    assert exc.value.details == {"available": 2, "required": 3}


def test_min_pool_checked_on_whole_pool() -> None:
    correct = media_item("C")
    pool = [correct, *pool_of(3)]
    with pytest.raises(PoolTooSmallError):
        PLUGIN.pick(correct, pool, count=2, params=params(min_pool=5), rng=rng())
    assert len(PLUGIN.pick(correct, pool, count=2, params=params(min_pool=4), rng=rng())) == 2


def test_deterministic_by_seed_and_independent_of_pool_order() -> None:
    correct = media_item("C")
    pool = pool_of(20)
    a = PLUGIN.pick(correct, pool, count=3, params=params(), rng=rng(b"x"))
    b = PLUGIN.pick(correct, list(reversed(pool)), count=3, params=params(), rng=rng(b"x"))
    c = PLUGIN.pick(correct, pool, count=3, params=params(), rng=rng(b"y"))
    assert [i.id for i in a] == [i.id for i in b]
    assert [i.id for i in a] != [i.id for i in c]


@settings(max_examples=60, deadline=None)
@given(
    n=st.integers(1, 7),
    size=st.integers(8, 30),
    seed=st.binary(min_size=1, max_size=8),
)
def test_property_distinct_without_correct(n: int, size: int, seed: bytes) -> None:
    correct = media_item("C")
    pool = [correct, *pool_of(size)]
    picked = PLUGIN.pick(correct, pool, count=n, params=params(), rng=SeededRandom(seed))
    ids = [i.id for i in picked]
    assert len(ids) == n == len(set(ids))
    assert correct.id not in ids


# ------------------------------------------------------------------ нормализация названий
@pytest.mark.parametrize(
    ("correct_title", "other_title"),
    [
        ("Ёлка", "елка"),  # «ё» = «е»
        ("Hello, World!", "hello world"),  # пунктуация
        ("THE MATRIX", "the matrix"),  # регистр
        ("Rock & Roll", "Rock and Roll"),  # замена союзов движка
    ],
)
def test_same_title_compared_after_normalization(correct_title: str, other_title: str) -> None:
    correct = media_item(correct_title, title_aliases=())
    twin = media_item(other_title, title_aliases=())
    others = pool_of(3)
    picked = PLUGIN.pick(correct, [correct, twin, *others], count=3, params=params(), rng=rng())
    assert twin.id not in {i.id for i in picked}


def test_alias_with_other_spelling_is_same_title() -> None:
    correct = media_item("Zed", title_aliases=())
    twin = media_item("Другое", title_aliases=("Zed!",))
    picked = PLUGIN.pick(correct, [correct, twin, *pool_of(3)], count=3, params=params(), rng=rng())
    assert twin.id not in {i.id for i in picked}


def test_candidates_with_equal_titles_are_deduplicated() -> None:
    """Два кандидата с одним названием не должны оказаться в одном раунде."""
    correct = media_item("C")
    dups = [media_item("Same Name"), media_item("same name!"), media_item("SAME NAME")]
    others = pool_of(2)
    for n in range(30):
        picked = PLUGIN.pick(
            correct, [correct, *dups, *others], count=3, params=params(), rng=rng(f"{n}".encode())
        )
        assert sum(1 for i in picked if i in dups) == 1


def test_dedup_counts_toward_shortage() -> None:
    correct = media_item("C")
    pool = [correct, media_item("Dup"), media_item("dup"), media_item("Other")]
    with pytest.raises(PoolTooSmallError) as exc:
        PLUGIN.pick(correct, pool, count=3, params=params(), rng=rng())
    assert exc.value.details == {"available": 2, "required": 3}


def test_pool_enough_but_filters_leave_too_few() -> None:
    """Пул не меньше ``min_pool``, но близких по фильтрам меньше ``count``."""
    correct = media_item("C", genres=("rock",))
    far = pool_of(10, genres=("jazz",))
    near = pool_of(1, genres=("rock",))
    with pytest.raises(PoolTooSmallError) as exc:
        PLUGIN.pick(
            correct,
            [correct, *near, *far],
            count=3,
            params=params(same_genre=True, min_pool=5),
            rng=rng(),
        )
    assert exc.value.details == {"available": 1, "required": 3}


def test_zero_popularity_is_not_division_error() -> None:
    correct = media_item("C", popularity=0.0)
    pool = [media_item(f"x{i}", popularity=0.0) for i in range(3)]
    picked = PLUGIN.pick(correct, pool, count=3, params=params(popularity_ratio=0.5), rng=rng())
    assert len(picked) == 3


def test_cross_check_min_pool() -> None:
    assert PLUGIN.cross_check(params(min_pool=4), count=3) == []
    assert [i.code for i in PLUGIN.cross_check(params(min_pool=3), count=3)] == [
        "config.distractor_pool_too_small"
    ]
