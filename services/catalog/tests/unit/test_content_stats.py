"""Статистика угадываний по контенту (ADR 0076): разбор событий, агрегаты, медиана."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from catalog_service.domain.content_stats import (
    InvalidStatsEventError,
    RoundOutcome,
    StatsRules,
    aggregate,
    approx_median_ms,
    observations_from_game_finished,
    observations_from_match_finished,
)

pytestmark = pytest.mark.unit

RULES = StatsRules(
    time_bucket_ms=1000,
    time_bucket_max=10,
    match_counted_finishes=frozenset({"completed"}),
    count_aborted_matches=False,
)
A, B = uuid.UUID(int=1), uuid.UUID(int=2)
U1, U2 = str(uuid.UUID(int=11)), str(uuid.UUID(int=12))


def game_round(
    item: uuid.UUID, outcome: str, level: int | None, elapsed: int | None
) -> dict[str, Any]:
    return {
        "index": 0,
        "media_item_id": str(item),
        "outcome": outcome,
        "level": level,
        "points": 0,
        "elapsed_ms": elapsed,
    }


def game(*rounds: dict[str, Any], mode: str = "music.random.classic") -> dict[str, Any]:
    return {"mode_key": mode, "rounds": list(rounds)}


def result(
    user: str, level: int | None, elapsed: int | None, *, partial: bool = False
) -> dict[str, Any]:
    return {"user_id": user, "level": level, "partial": partial, "score": 0, "elapsed_ms": elapsed}


def match(
    rounds: list[dict[str, Any]],
    *,
    finishes: tuple[str, str] = ("completed", "completed"),
    aborted: bool = False,
) -> dict[str, Any]:
    return {
        "mode_key": "music.duel.classic",
        "aborted": aborted,
        "players": [{"user_id": U1, "finish": finishes[0]}, {"user_id": U2, "finish": finishes[1]}],
        "rounds": [
            {"index": i, "media_item_id": str(item), "results": res}
            for i, (item, res) in enumerate(rounds)
        ],
    }


# ----------------------------------------------------------------------------- соло
def test_game_rounds_become_observations_and_void_is_not_shown() -> None:
    payload = game(
        game_round(A, "exact", 1, 2500),
        game_round(A, "partial", 2, 9000),
        game_round(B, "miss", None, 30000),
        game_round(B, "gave_up", None, None),
        game_round(B, "void", None, None),
    )
    got = observations_from_game_finished(payload)
    assert [o.outcome for o in got] == [
        RoundOutcome.EXACT,
        RoundOutcome.PARTIAL,
        RoundOutcome.MISS,
        RoundOutcome.GAVE_UP,
    ]
    assert {o.mode_key for o in got} == {"music.random.classic"}
    assert (got[0].level, got[0].elapsed_ms) == (1, 2500)


def test_broken_game_payload_is_rejected() -> None:
    with pytest.raises(InvalidStatsEventError):
        observations_from_game_finished({"rounds": []})
    with pytest.raises(InvalidStatsEventError):
        observations_from_game_finished(game(game_round(A, "weird", None, None)))


# ----------------------------------------------------------------------------- онлайн
def test_match_results_map_to_outcomes() -> None:
    payload = match(
        [
            (A, [result(U1, 1, 4210), result(U2, None, None)]),
            (B, [result(U1, 2, 9000, partial=True), result(U2, 2, 21050)]),
        ]
    )
    got = observations_from_match_finished(payload, RULES)
    assert [(o.media_item_id, o.outcome) for o in got] == [
        (A, RoundOutcome.EXACT),
        (A, RoundOutcome.MISS),
        (B, RoundOutcome.PARTIAL),
        (B, RoundOutcome.EXACT),
    ]
    assert {o.mode_key for o in got} == {"music.duel.classic"}


def test_match_skips_players_who_did_not_complete_and_aborted_matches() -> None:
    rounds = [(A, [result(U1, 1, 100), result(U2, None, None)])]
    got = observations_from_match_finished(match(rounds, finishes=("completed", "forfeit")), RULES)
    assert [o.outcome for o in got] == [RoundOutcome.EXACT]
    assert observations_from_match_finished(match(rounds, aborted=True), RULES) == []


def test_aborted_matches_can_be_counted_by_config() -> None:
    rules = StatsRules(
        time_bucket_ms=1000,
        time_bucket_max=10,
        match_counted_finishes=frozenset({"completed"}),
        count_aborted_matches=True,
    )
    rounds = [(A, [result(U1, 1, 100), result(U2, 1, 200)])]
    assert len(observations_from_match_finished(match(rounds, aborted=True), rules)) == 2


def test_broken_match_results_are_rejected() -> None:
    for bad in ("oops", ["oops"]):
        payload = match([(A, [])])
        payload["rounds"][0]["results"] = bad
        with pytest.raises(InvalidStatsEventError):
            observations_from_match_finished(payload, RULES)


# ----------------------------------------------------------------------------- агрегаты
def test_aggregate_sums_per_item_and_mode_with_level_and_time_buckets() -> None:
    payload = game(
        game_round(A, "exact", 1, 2500),
        game_round(A, "exact", 2, 2999),
        game_round(A, "miss", None, 30000),
        game_round(A, "partial", 1, 1000),
        game_round(A, "gave_up", None, None),
        game_round(B, "exact", 1, 999_999),
    )
    deltas = aggregate(observations_from_game_finished(payload), RULES)
    a = deltas[(A, "music.random.classic")]
    assert (a.shown, a.exact, a.partial, a.miss, a.gave_up) == (5, 2, 1, 1, 1)
    assert (a.time_sum_ms, a.time_count) == (5499, 2)
    assert a.level_buckets == {1: 1, 2: 1}
    assert a.time_buckets == {2: 2}
    # Время сверх последней корзины попадает в неё (без бесконечного роста строк).
    assert deltas[(B, "music.random.classic")].time_buckets == {10: 1}


def test_aggregate_ignores_missing_time_and_level_of_a_non_exact_answer() -> None:
    deltas = aggregate(
        observations_from_game_finished(
            game(game_round(A, "exact", 1, None), game_round(A, "partial", 2, 500))
        ),
        RULES,
    )
    d = deltas[(A, "music.random.classic")]
    assert (d.time_sum_ms, d.time_count, d.time_buckets) == (0, 0, {})
    assert d.level_buckets == {1: 1}


def test_overflow_bucket_collects_long_answers() -> None:
    long_answer = game(game_round(A, "exact", 1, 10_000_000))
    deltas = aggregate(observations_from_game_finished(long_answer), RULES)
    assert deltas[(A, "music.random.classic")].time_buckets == {RULES.time_bucket_max: 1}


# ----------------------------------------------------------------------------- медиана
@pytest.mark.parametrize(
    ("buckets", "expected"),
    [
        ({}, None),
        ({2: 1}, 2500),
        ({0: 1, 4: 1, 9: 1}, 4500),
        ({1: 2, 5: 2}, 1500),
    ],
)
def test_approx_median_uses_bucket_midpoints(buckets: dict[int, int], expected: int | None) -> None:
    assert approx_median_ms(buckets, bucket_ms=1000) == expected
