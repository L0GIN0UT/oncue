"""Сложность (K4, D9) и правила видимости (D6) — чистые функции домена."""

from __future__ import annotations

import pytest
from collections_service.domain.access import Viewer, can_view, custom_visible, items_visible
from collections_service.domain.difficulty import Difficulty, DifficultyModel
from collections_service.domain.model import Status
from hypothesis import given
from hypothesis import strategies as st

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

MODEL = DifficultyModel(prior=0.5, m=10, min_plays=10, thresholds=(0.4, 0.7))


def test_no_difficulty_below_min_plays() -> None:
    assert MODEL.score(9.0, 9) is None
    assert MODEL.bucket(None) is None
    assert MODEL.score(10.0, 10) == 0.25


@given(
    plays=st.integers(min_value=10, max_value=10_000),
    mean=st.floats(min_value=0, max_value=1),
    delta=st.floats(min_value=0.001, max_value=1),
)
def test_more_points_means_easier(plays: int, mean: float, delta: float) -> None:
    """Монотонность: выше средняя доля очков — ниже сложность; значения в [0, 1]."""
    worse = MODEL.score(mean * plays, plays)
    better = MODEL.score(min(1.0, mean + delta) * plays, plays)
    assert worse is not None and better is not None
    assert 0 <= better <= worse <= 1


@given(plays=st.integers(min_value=10, max_value=10_000))
def test_prior_pulls_small_samples_to_middle(plays: int) -> None:
    """Все набрали максимум: сложность не 0 сразу, а стремится к 0 с ростом числа игр."""
    score = MODEL.score(float(plays), plays)
    assert score is not None
    assert 0 < score <= 0.25
    more = MODEL.score(float(plays * 2), plays * 2)
    assert more is not None and more < score


@pytest.mark.parametrize(
    ("score", "bucket"),
    [
        (0.0, Difficulty.EASY),
        (0.39, Difficulty.EASY),
        (0.4, Difficulty.MEDIUM),
        (0.69, Difficulty.MEDIUM),
        (0.7, Difficulty.HARD),
        (1.0, Difficulty.HARD),
    ],
)
def test_buckets(score: float, bucket: Difficulty) -> None:
    assert MODEL.bucket(score) is bucket
    low, high = MODEL.bounds(bucket)
    assert low <= score < high


def test_zero_weight_prior() -> None:
    model = DifficultyModel(prior=0.5, m=0, min_plays=1, thresholds=(0.4, 0.7))
    assert model.score(0.0, 1) == 1.0


AUTHOR = uuid7()
ANON = Viewer.anonymous()
PLAYER = Viewer(user_id=uuid7())
OWNER = Viewer(user_id=AUTHOR)
ADMIN = Viewer(user_id=uuid7(), is_admin=True)


@pytest.mark.parametrize(
    ("status", "viewer", "visible"),
    [
        (Status.PUBLISHED, ANON, True),
        (Status.HIDDEN, ANON, False),
        (Status.HIDDEN, PLAYER, False),
        (Status.HIDDEN, OWNER, True),
        (Status.HIDDEN, ADMIN, True),
        (Status.DRAFT, OWNER, False),
        (Status.DELETED, ADMIN, False),
    ],
)
def test_can_view(status: Status, viewer: Viewer, visible: bool) -> None:
    assert can_view(status, AUTHOR, viewer) is visible


@pytest.mark.parametrize(
    ("viewer", "completed", "items", "custom"),
    [
        (ANON, True, False, False),
        (PLAYER, False, False, False),
        (PLAYER, True, True, False),
        (OWNER, False, True, True),
        (ADMIN, False, True, True),
    ],
)
def test_items_and_custom_visibility(
    viewer: Viewer, completed: bool, items: bool, custom: bool
) -> None:
    assert items_visible(AUTHOR, viewer, completed=completed) is items
    assert custom_visible(AUTHOR, viewer) is custom


def test_curated_without_author_is_not_owned_by_anyone() -> None:
    assert custom_visible(None, PLAYER) is False
    assert items_visible(None, PLAYER, completed=False) is False
