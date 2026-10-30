"""``EngineFactory.validate`` для онлайн-сессий (A1, ADR 0032): сломанный конфиг на каждое
правило, виды сессий сервиса, соло-конфиги без изменений."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from gv_game_kernel import (
    ALL_SESSIONS,
    ONLINE_SESSIONS,
    SOLO_SESSIONS,
    ConfigError,
    ConfigIssue,
    EngineFactory,
    ModeNotOnlineError,
    PluginRegistry,
    parse_mode_config,
    register_builtin,
    register_collection,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
REGISTRY = register_collection(register_builtin(PluginRegistry()))
MATCH = EngineFactory(REGISTRY, supported_sessions=ONLINE_SESSIONS)
GAME = EngineFactory(REGISTRY, supported_sessions=ALL_SESSIONS)
SOLO = EngineFactory(REGISTRY)
ONLINE = ("film.duel.frame", "film.party.frame", "film.lobby.collection")
_SEED_FILES = sorted((Path(__file__).resolve().parents[3] / "seeds/game/modes").glob("*.yaml"))
# Сиды делятся по ``session.kind``: соло (game) и онлайн-шаблоны 4a (match, ADR 0032).
SEEDS = [
    p.stem
    for p in _SEED_FILES
    if yaml.safe_load(p.read_text(encoding="utf-8"))["session"]["kind"] == "solo"
]
ONLINE_SEEDS = [p.stem for p in _SEED_FILES if p.stem not in SEEDS]

FRAME_LEVELS = [
    {"blur_px": 25, "crop": 0.35, "max_width": 960},
    {"blur_px": 20, "crop": 0.5, "max_width": 960},
    {"blur_px": 15, "crop": 0.7, "max_width": 960},
    {"blur_px": 10, "crop": 1.0, "max_width": 960},
    {"blur_px": 5, "crop": 1.0, "max_width": 960},
    {"blur_px": 0, "crop": 1.0, "max_width": 960},
]


def _issues(body: dict[str, Any], factory: EngineFactory = MATCH) -> list[ConfigIssue]:
    return factory.validate(parse_mode_config(body))


def _set(body: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    node = body
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    return body


def test_session_kind_sets() -> None:
    assert {"solo"} == SOLO_SESSIONS
    assert {"duel", "party", "lobby"} == ONLINE_SESSIONS
    assert ALL_SESSIONS == SOLO_SESSIONS | ONLINE_SESSIONS


@pytest.mark.parametrize("key", ONLINE)
def test_online_templates_valid_for_match_and_game(key: str, seed_body: SeedBody) -> None:
    config = parse_mode_config(seed_body(key))
    assert MATCH.validate(config) == []
    assert GAME.validate(config) == []
    # game по умолчанию играет только соло: онлайн-режим — session_unsupported.
    assert ConfigIssue("session.kind", "config.session_unsupported") in SOLO.validate(config)
    engine = MATCH.build(config)
    assert engine.contest is not None
    assert engine.round_rules.give_up_allowed is False


def test_online_seeds_of_stage4a_exist() -> None:
    assert set(ONLINE_SEEDS) >= {
        "music.duel.classic",
        "film.duel.frame",
        "film.party.frame",
        "music.lobby.classic",
        "film.lobby.collection",
    }


@pytest.mark.parametrize("key", ONLINE_SEEDS)
def test_online_seeds_valid_for_match_and_game(key: str, seed_body: SeedBody) -> None:
    config = parse_mode_config(seed_body(key))
    assert MATCH.validate(config) == []
    assert GAME.validate(config) == []
    assert MATCH.build(config).contest is not None


@pytest.mark.parametrize("key", SEEDS)
def test_solo_seeds_rejected_by_match_but_valid_for_game(key: str, seed_body: SeedBody) -> None:
    config = parse_mode_config(seed_body(key))
    assert GAME.validate(config) == []
    assert MATCH.validate(config) == [ConfigIssue("session.kind", "config.session_unsupported")]
    with pytest.raises(ModeNotOnlineError) as info:
        _ = GAME.build(config).online_session
    assert info.value.code == "game.mode_not_online"


# (конфиг, путь в теле, новое значение, ожидаемая проблема)
BROKEN: list[tuple[str, str, Any, ConfigIssue]] = [
    (
        "film.duel.frame",
        "session.contest.type",
        "relay",
        ConfigIssue("session.contest.type", "config.unknown_plugin"),
    ),
    (
        "film.duel.frame",
        "session.contest.params",
        {"attempts_per_level": 9, "lock_on_first_correct": True},
        ConfigIssue("session.contest.params.attempts_per_level", "schema.less_than_equal"),
    ),
    (
        "film.duel.frame",
        "session.contest.params",
        {"attempts_per_level": 3},
        ConfigIssue("session.contest.params.lock_on_first_correct", "schema.missing"),
    ),
    (
        "film.party.frame",
        "session.players",
        {"min": 5, "max": 3},
        ConfigIssue("session.players.min", "config.players_range"),
    ),
    (
        "film.party.frame",
        "session.players",
        {"min": 3, "max": 7},
        ConfigIssue("session.players.max", "schema.less_than_equal"),
    ),
    (
        "film.party.frame",
        "session.level_windows_s",
        [20, 20, 20],
        ConfigIssue("session.level_windows_s", "config.levels_mismatch"),
    ),
    (
        "film.party.frame",
        "session.ranked",
        True,
        ConfigIssue("session.ladder", "config.ladder_required"),
    ),
    (
        "film.party.frame",
        "session.lobby",
        {"overridable": {"rounds": [10]}},
        ConfigIssue("session.lobby", "config.lobby_unexpected"),
    ),
    (
        "film.lobby.collection",
        "session.lobby",
        None,
        ConfigIssue("session.lobby", "config.lobby_required"),
    ),
    (
        "film.party.frame",
        "session.chat",
        {"free_text": True},
        ConfigIssue("session.chat.free_text", "config.free_text_lobby_only"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.rounds",
        [5, 10],
        ConfigIssue("session.lobby.overridable.rounds", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.level_window_s",
        [15, 40],
        ConfigIssue("session.lobby.overridable.level_window_s", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.level_windows_s",
        [25, 25, 25, 25, 25, 20],
        ConfigIssue("session.lobby.overridable.level_window_s", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.players_max",
        [2, 4],
        ConfigIssue("session.lobby.overridable.players_max", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.players.min",
        3,
        ConfigIssue("session.lobby.overridable.players_max[0]", "config.players_range"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.visibility",
        ["public"],
        ConfigIssue("session.lobby.overridable.visibility", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.answer_format",
        ["pick"],
        ConfigIssue("session.lobby.overridable.answer_format", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.answer_format",
        ["classic", "telepathy"],
        ConfigIssue("session.lobby.overridable.answer_format[1]", "config.unknown_plugin"),
    ),
    (
        # Источник collection строит только classic-раунды.
        "film.lobby.collection",
        "session.lobby.overridable.answer_format",
        ["classic", "pick"],
        ConfigIssue(
            "session.lobby.overridable.answer_format[1]", "config.answer_format_unsupported"
        ),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [{"type": "poster_masked", "params": {}}],
        ConfigIssue("session.lobby.overridable.clue", "config.override_default_missing"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
            {"type": "hologram", "params": {}},
        ],
        ConfigIssue("session.lobby.overridable.clue[1].type", "config.unknown_plugin"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
            {"type": "audio_intro", "params": {"start_s": 0, "levels": [{"seconds": 1}]}},
        ],
        ConfigIssue("session.lobby.overridable.clue[1].type", "config.domain_unsupported"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
            {"type": "frame_progressive", "params": {"levels": []}},
        ],
        ConfigIssue("session.lobby.overridable.clue[1].params.levels", "schema.too_short"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS[:3]}},
        ],
        ConfigIssue("session.lobby.overridable.clue[1].params.levels", "config.levels_mismatch"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
            {
                "type": "frame_progressive",
                "params": {"levels": FRAME_LEVELS},
                "reveal": {"type": "hologram", "params": {}},
            },
        ],
        ConfigIssue("session.lobby.overridable.clue[1].reveal.type", "config.unknown_plugin"),
    ),
    (
        "film.lobby.collection",
        "session.lobby.overridable.clue",
        [
            {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
            {
                "type": "frame_progressive",
                "params": {"levels": FRAME_LEVELS},
                "reveal": {"type": "audio_intro", "params": {"levels": [{"seconds": 15}]}},
            },
        ],
        ConfigIssue("session.lobby.overridable.clue[1].reveal.type", "config.domain_unsupported"),
    ),
    (
        "film.duel.frame",
        "session.end",
        {"type": "all_rounds", "params": {}},
        ConfigIssue("session.end", "schema.extra_forbidden"),
    ),
    (
        "music.daily.classic",
        "scoring",
        {"type": "party_speed", "params": {"max_points": 100, "partial_ratio": 0.5}},
        ConfigIssue("scoring.params", "config.online_only"),
    ),
]


@pytest.mark.parametrize(
    ("key", "path", "value", "issue"), BROKEN, ids=[f"{b[3].code}:{b[3].path}" for b in BROKEN]
)
def test_broken_online_config_reports_issue(
    key: str, path: str, value: Any, issue: ConfigIssue, seed_body: SeedBody
) -> None:
    body = _set(seed_body(key), path, value)
    try:
        issues = _issues(body, GAME)
    except ConfigError as exc:
        issues = [ConfigIssue(e["path"], e["code"]) for e in exc.details["errors"]]
    assert issue in issues, issues


def test_every_online_rule_code_is_covered() -> None:
    codes = {b[3].code for b in BROKEN}
    assert codes >= {
        "config.unknown_plugin",
        "config.players_range",
        "config.levels_mismatch",
        "config.ladder_required",
        "config.lobby_required",
        "config.lobby_unexpected",
        "config.free_text_lobby_only",
        "config.override_default_missing",
        "config.answer_format_unsupported",
        "config.domain_unsupported",
        "config.online_only",
    }


def test_ranked_with_ladder_is_valid(seed_body: SeedBody) -> None:
    body = seed_body("film.duel.frame")
    body["session"].update(ranked=True, ladder="film.classic")
    assert _issues(body) == []


def test_race_rejects_pick_format_parallel_accepts_it(seed_body: SeedBody) -> None:
    """Совместимость состязания с форматом — атрибут ``ContestRule.answer_formats``."""
    body = seed_body("film.duel.frame")
    body["answer"]["format"] = "pick"
    assert ConfigIssue("session.contest.type", "config.answer_format_unsupported") in _issues(body)
    body["session"]["contest"] = {
        "type": "parallel",
        "params": {"attempts_per_level": 3, "end_when_all_answered": True},
    }
    issues = _issues(body)
    assert ConfigIssue("session.contest.type", "config.answer_format_unsupported") not in issues
    # pick — один ответ на раунд: одна попытка уровня у состязания.
    assert (
        ConfigIssue("session.contest.params.attempts_per_level", "config.single_answer_levels")
        in issues
    )


def test_unknown_contest_skips_its_params(seed_body: SeedBody) -> None:
    body = seed_body("film.duel.frame")
    body["session"]["contest"] = {"type": "relay", "params": {"x": 1}}
    assert _issues(body) == [ConfigIssue("session.contest.type", "config.unknown_plugin")]


def test_lobby_variants_valid_when_compatible(seed_body: SeedBody) -> None:
    body = seed_body("film.lobby.collection")
    over = body["session"]["lobby"]["overridable"]
    over["answer_format"] = ["classic"]
    over["clue"] = [
        {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
        {
            "type": "frame_progressive",
            "params": {"levels": FRAME_LEVELS},
            "reveal": {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS[-1:]}},
        },
    ]
    assert _issues(body) == []


def test_build_refuses_invalid_online_config(seed_body: SeedBody) -> None:
    body = _set(seed_body("film.party.frame"), "session.level_windows_s", [20])
    with pytest.raises(ConfigError) as info:
        MATCH.build(parse_mode_config(body))
    assert info.value.details == {
        "errors": [{"path": "session.level_windows_s", "code": "config.levels_mismatch"}]
    }
