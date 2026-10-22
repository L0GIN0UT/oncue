"""``MatchOutcome.rank`` (A4, D4): места с ничьими, вышедшие после оставшихся, исход дуэли."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_game_kernel import DuelOutcome, MatchOutcome

pytestmark = pytest.mark.unit

W, L, D = DuelOutcome.WIN, DuelOutcome.LOSS, DuelOutcome.DRAW

# Случаи: очки игроков, вышедшие и ожидаемые места (игрок, место, исход дуэли, вышел).
CASES: list[tuple[dict[str, int], set[str], list[tuple[str, int, DuelOutcome | None, bool]]]] = [
    ({}, set(), []),
    ({"a": 5}, set(), [("a", 1, None, False)]),
    ({"a": 10, "b": 7}, set(), [("a", 1, W, False), ("b", 2, L, False)]),
    ({"a": 3, "b": 9}, set(), [("b", 1, W, False), ("a", 2, L, False)]),
    ({"b": 6, "a": 6}, set(), [("a", 1, D, False), ("b", 1, D, False)]),
    # Сдавшийся/вышедший в дуэли проигрывает даже с большим счётом.
    ({"a": 12, "b": 0}, {"a"}, [("b", 1, W, False), ("a", 2, L, True)]),
    ({"a": 4, "b": 4}, {"a", "b"}, [("a", 1, D, True), ("b", 1, D, True)]),
    (
        {"a": 30, "b": 30, "c": 10, "d": 5},
        set(),
        [
            ("a", 1, None, False),
            ("b", 1, None, False),
            ("c", 3, None, False),
            ("d", 4, None, False),
        ],
    ),
    (
        {"a": 10, "b": 10, "c": 10},
        set(),
        [("a", 1, None, False), ("b", 1, None, False), ("c", 1, None, False)],
    ),
    (
        {"a": 50, "b": 20, "c": 20, "d": 40, "e": 40},
        {"d", "e"},
        [
            ("a", 1, None, False),
            ("b", 2, None, False),
            ("c", 2, None, False),
            ("d", 4, None, True),
            ("e", 4, None, True),
        ],
    ),
    (
        {"a": 1, "b": 99, "c": 3},
        {"b"},
        [("c", 1, None, False), ("a", 2, None, False), ("b", 3, None, True)],
    ),
]


@pytest.mark.parametrize(("scores", "left", "expected"), CASES, ids=str)
def test_rank_table(
    scores: dict[str, int],
    left: set[str],
    expected: list[tuple[str, int, DuelOutcome | None, bool]],
) -> None:
    got = MatchOutcome.rank(scores, left=left)
    assert [(p.player_id, p.place, p.outcome, p.left) for p in got] == expected
    assert all(p.score == scores[p.player_id] for p in got)


def test_rank_rejects_unknown_left_player() -> None:
    with pytest.raises(ValueError, match="вышедших"):
        MatchOutcome.rank({"a": 1}, left={"z"})


@given(
    scores=st.dictionaries(
        st.sampled_from("abcdef"), st.integers(min_value=0, max_value=50), max_size=6
    ),
    data=st.data(),
)
def test_rank_properties(scores: dict[str, int], data: st.DataObject) -> None:
    left = data.draw(st.sets(st.sampled_from(sorted(scores)))) if scores else set()
    got = MatchOutcome.rank(scores, left=left)
    assert sorted(p.player_id for p in got) == sorted(scores)
    places = [p.place for p in got]
    assert places == sorted(places)
    # Оставшиеся всегда выше вышедших; внутри группы больше очков — не ниже место.
    stayed_last = max((p.place for p in got if not p.left), default=0)
    assert all(p.place > stayed_last for p in got if p.left)
    for x in got:
        for y in got:
            if x.left == y.left and x.score > y.score:
                assert x.place < y.place
            if x.left == y.left and x.score == y.score:
                assert x.place == y.place
    # Место = 1 + число игроков строго выше (соревновательная нумерация 1, 1, 3).
    for p in got:
        assert p.place == 1 + sum(1 for q in got if q.place < p.place)
