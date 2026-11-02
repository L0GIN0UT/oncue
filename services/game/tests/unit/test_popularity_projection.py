"""Популярность единицы в проекции game (ADR 0074): ``popularity``, иначе ``listens``."""

from __future__ import annotations

import pytest
from game_service.infrastructure.messaging import _popularity

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        ({"popularity": 123.456}, 123.456),
        ({"listens": 987654}, 987654.0),
        ({"popularity": 12.5, "listens": 987654}, 12.5),
        ({"popularity": "high", "listens": 7}, 7.0),
        ({"popularity": True, "listens": 7}, 7.0),
        ({"listens": "many"}, None),
        ({"vote_average": 7.0}, None),
        ({}, None),
        (None, None),
    ],
)
def test_popularity_prefers_popularity_then_listens(
    metrics: dict[str, object] | None, expected: float | None
) -> None:
    assert _popularity(metrics) == expected
