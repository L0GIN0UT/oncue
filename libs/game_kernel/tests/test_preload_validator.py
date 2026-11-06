"""Флаг ``features.preload_next_round`` (предзагрузка уровня 1 следующего раунда).

Безопасен только там, где у клиента нет выигрыша от раннего доступа к ссылке: без скорера по
времени и не в рейтинге (ADR 0077, 0078). Соло и нерейтинговые онлайн-режимы с очками по уровню
флаг принимают (ADR 0080); рейтинговые и скорер по времени (``by_speed``, ``party_speed``) - нет.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gv_game_kernel import (
    ALL_SESSIONS,
    ConfigIssue,
    EngineFactory,
    PluginRegistry,
    parse_mode_config,
    register_builtin,
    register_collection,
)
from gv_game_kernel.builtin.scoring import (
    ByLevelScorer,
    BySpeedScorer,
    NoneScorer,
    PartySpeedScorer,
    StreakScorer,
)
from gv_game_kernel.registry import PRELOAD_FEATURE, PRELOAD_UNSAFE, UNKNOWN_PLUGIN

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
FACTORY = EngineFactory(
    register_collection(register_builtin(PluginRegistry())), supported_sessions=ALL_SESSIONS
)
FLAG = f"features.{PRELOAD_FEATURE}"


def _issues(body: dict[str, Any]) -> list[ConfigIssue]:
    return FACTORY.validate(parse_mode_config(body))


def test_flag_name_and_code_are_stable_contract() -> None:
    assert PRELOAD_FEATURE == "preload_next_round"
    assert PRELOAD_UNSAFE == "config.preload_unsafe"


def test_solo_by_level_accepts_the_flag(seed_body: SeedBody) -> None:
    body = seed_body("film.random.frame")
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert _issues(body) == []


def test_flag_is_off_by_default_and_false_is_always_valid(seed_body: SeedBody) -> None:
    body = seed_body("film.ranked.classic")
    body.setdefault("features", {})[PRELOAD_FEATURE] = False
    assert _issues(body) == []


@pytest.mark.parametrize("key", ["film.ranked.classic", "music.ranked.lite"])
def test_ranked_rejects_the_flag(key: str, seed_body: SeedBody) -> None:
    body = seed_body(key)
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert ConfigIssue(FLAG, PRELOAD_UNSAFE) in _issues(body)


@pytest.mark.parametrize("key", ["film.duel.frame", "music.duel.classic"])
def test_unranked_online_by_level_accepts_the_flag(key: str, seed_body: SeedBody) -> None:
    body = seed_body(key)
    assert body["session"].get("ranked", False) is False
    assert body["scoring"]["type"] == "by_level"
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert _issues(body) == []


@pytest.mark.parametrize(
    "key", ["film.duel.lite", "film.party.frame", "film.lobby.collection", "music.lobby.classic"]
)
def test_online_time_based_modes_reject_the_flag(key: str, seed_body: SeedBody) -> None:
    body = seed_body(key)
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert ConfigIssue(FLAG, PRELOAD_UNSAFE) in _issues(body)


def test_ranked_by_level_rejects_the_flag_even_though_scorer_is_safe(seed_body: SeedBody) -> None:
    body = seed_body("music.ranked.classic")
    assert body["scoring"]["type"] == "by_level"
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert _issues(body) == [ConfigIssue(FLAG, PRELOAD_UNSAFE)]


def test_time_based_scorer_rejects_the_flag(seed_body: SeedBody) -> None:
    body = seed_body("film.random.frame")
    body["scoring"] = {
        "type": "by_speed",
        "params": {"max_points": 100, "min_points": 10, "window_s": 30.0, "partial_ratio": 0.5},
    }
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert ConfigIssue(FLAG, PRELOAD_UNSAFE) in _issues(body)


def test_scorers_declare_whether_they_depend_on_time() -> None:
    assert {
        s.key: s.time_based
        for s in (ByLevelScorer, BySpeedScorer, StreakScorer, NoneScorer, PartySpeedScorer)
    } == {
        "by_level": False,
        "by_speed": True,
        "streak": False,
        "none": False,
        "party_speed": True,
    }


def test_unknown_scorer_reports_only_the_scorer_problem(seed_body: SeedBody) -> None:
    body = seed_body("film.random.frame")
    body["scoring"] = {"type": "no_such_scorer", "params": {}}
    body["features"][PRELOAD_FEATURE] = True
    issues = _issues(body)
    assert ConfigIssue("scoring.type", UNKNOWN_PLUGIN) in issues
    assert ConfigIssue(FLAG, PRELOAD_UNSAFE) not in issues  # судить о времени не по чему


def test_ranked_with_time_scorer_reports_the_flag_once(seed_body: SeedBody) -> None:
    body = seed_body("film.ranked.lite")  # party_speed: рейтинг и скорер по времени
    body.setdefault("features", {})[PRELOAD_FEATURE] = True
    assert _issues(body).count(ConfigIssue(FLAG, PRELOAD_UNSAFE)) == 1
