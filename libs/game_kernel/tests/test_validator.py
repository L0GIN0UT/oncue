"""``EngineFactory.validate`` на сидах и на сломанных конфигах: код и путь на каждое правило."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gv_game_kernel import (
    ConfigError,
    ConfigIssue,
    EngineFactory,
    PluginRegistry,
    parse_mode_config,
    register_builtin,
    register_collection,
)
from gv_game_kernel.builtin import AudioIntroParams

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
SEEDS = sorted(
    p.stem for p in (Path(__file__).resolve().parents[3] / "seeds/game/modes").glob("*.yaml")
)
FACTORY = EngineFactory(register_builtin(PluginRegistry()))
# Сиды game собираются реестром процесса game: встроенные плагины и ``collection`` (3b).
SEED_FACTORY = EngineFactory(register_collection(register_builtin(PluginRegistry())))


def _issues(body: dict[str, Any], factory: EngineFactory = FACTORY) -> list[ConfigIssue]:
    return factory.validate(parse_mode_config(body))


@pytest.mark.parametrize("key", SEEDS)
def test_seeds_are_valid_and_build(key: str, seed_body: SeedBody) -> None:
    config = parse_mode_config(seed_body(key))
    assert SEED_FACTORY.validate(config) == []
    engine = SEED_FACTORY.build(config)
    if engine.answer_format.single_answer:
        # Формат pick (пара, сетка): один уровень и один ответ на раунд.
        assert engine.levels_count == 1
        assert engine.max_score() == engine.round_max_score() * config.session.rounds
    else:
        assert engine.levels_count == 6
        assert engine.max_score() == 6 * config.session.rounds


def _set(body: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    node = body
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    return body


# (сид, путь в теле, новое значение, ожидаемая проблема)
BROKEN: list[tuple[str, str, Any, ConfigIssue]] = [
    (
        "music.daily.classic",
        "source.type",
        "lottery",
        ConfigIssue("source.type", "config.unknown_plugin"),
    ),
    (
        "music.daily.classic",
        "clue.type",
        "hologram",
        ConfigIssue("clue.type", "config.unknown_plugin"),
    ),
    (
        "music.daily.classic",
        "answer.matcher.type",
        "embeddings",
        ConfigIssue("answer.matcher.type", "config.unknown_plugin"),
    ),
    (
        "music.daily.classic",
        "scoring.type",
        "party_speed",
        ConfigIssue("scoring.type", "config.unknown_plugin"),
    ),
    (
        "music.daily.classic",
        "answer.partial",
        ["franchise"],
        ConfigIssue("answer.partial[0]", "config.unknown_plugin"),
    ),
    (
        "music.daily.classic",
        "answer.partial",
        ["artist", "artist"],
        ConfigIssue("answer.partial[1]", "config.duplicate_partial"),
    ),
    (
        "film.daily.frame",
        "answer.partial",
        ["artist"],
        ConfigIssue("answer.partial[0]", "config.partial_domain_unsupported"),
    ),
    ("music.daily.classic", "domain", "film", ConfigIssue("domain", "config.domain_unsupported")),
    (
        "music.daily.classic",
        "source.params.size",
        4,
        ConfigIssue("source.params.size", "config.rounds_mismatch"),
    ),
    (
        "music.daily.classic",
        "source.params.pool.min_playable",
        3,
        ConfigIssue("source.params.pool.min_playable", "config.min_playable_below_rounds"),
    ),
    (
        "music.daily.classic",
        "source.params.timezone",
        "Mars/Base",
        ConfigIssue("source.params.timezone", "schema.timezone_unknown"),
    ),
    (
        "music.daily.classic",
        "source.params.size",
        "5",
        ConfigIssue("source.params.size", "schema.int_type"),
    ),
    (
        "music.daily.classic",
        "source.params.extra",
        1,
        ConfigIssue("source.params.extra", "schema.extra_forbidden"),
    ),
    (
        "music.daily.classic",
        "source.params.pool.years",
        {"from": 2010, "to": 2000},
        ConfigIssue("source.params.pool.years", "schema.years_range"),
    ),
    (
        "music.daily.classic",
        "clue.params.levels",
        [],
        ConfigIssue("clue.params.levels", "schema.too_short"),
    ),
    (
        "music.daily.classic",
        "answer.matcher.params.typo",
        [{"min_len": 5}],
        ConfigIssue("answer.matcher.params.typo[0].max_distance", "schema.missing"),
    ),
    (
        "music.daily.classic",
        "scoring.params.points",
        [6, 5, 4],
        ConfigIssue("scoring.params.points", "config.levels_mismatch"),
    ),
    (
        "music.daily.classic",
        "scoring.params.points",
        [6, 5, 4, 3, 2, 7],
        ConfigIssue("scoring.params.points[5]", "config.points_increasing"),
    ),
    (
        "music.daily.classic",
        "scoring.params.partial_ratio",
        1.5,
        ConfigIssue("scoring.params.partial_ratio", "schema.less_than_equal"),
    ),
    (
        "music.daily.classic",
        "scoring",
        {
            "type": "by_speed",
            "params": {"max_points": 10, "min_points": 50, "window_s": 20, "partial_ratio": 0.5},
        },
        ConfigIssue("scoring.params.min_points", "config.min_above_max"),
    ),
    (
        "film.random.frame",
        "source.params.presets",
        [
            {"id": "a", "title_i18n": "p.a", "filters": {}},
            {"id": "a", "title_i18n": "p.b", "filters": {}},
        ],
        ConfigIssue("source.params.presets[1].id", "config.duplicate_preset"),
    ),
    (
        "film.random.frame",
        "source.params.presets",
        [{"id": "a", "title_i18n": "p.a", "filters": {"kinds": ["track"]}}],
        ConfigIssue("source.params.presets[0].filters.kinds", "config.preset_filter_empty"),
    ),
    (
        "music.random.classic",
        "source.params.pool.genres_any",
        ["pop"],
        ConfigIssue("source.params.presets[2].filters.genres_any", "config.preset_filter_empty"),
    ),
    (
        "music.random.classic",
        "source.params.pool.years",
        {"from": 2015},
        ConfigIssue("source.params.presets[0].filters.years", "config.preset_filter_empty"),
    ),
    # --------------------------------------------------------------- этап 3a (A6)
    (
        "film.pair.rating",
        "answer.format",
        "classic",
        ConfigIssue("source.type", "config.answer_format_unsupported"),
    ),
    (
        "film.pair.rating",
        "answer.format",
        "classic",
        ConfigIssue("answer.matcher.type", "config.answer_format_unsupported"),
    ),
    (
        "music.daily.classic",
        "answer.format",
        "pick",
        ConfigIssue("source.type", "config.answer_format_unsupported"),
    ),
    (
        "music.marathon.artist",
        "answer.format",
        "pick",
        ConfigIssue("clue.params.levels", "config.single_answer_levels"),
    ),
    (
        "film.pair.rating",
        "session.attempts_per_level",
        2,
        ConfigIssue("session.attempts_per_level", "config.single_answer_levels"),
    ),
    (
        "film.pair.rating",
        "answer.partial",
        ["artist"],
        ConfigIssue("answer.partial", "config.partial_unsupported"),
    ),
    (
        "film.bracket.weekly",
        "answer.matcher.type",
        "metric_compare",
        ConfigIssue("answer.matcher.type", "config.comparison_required"),
    ),
    (
        "film.pair.rating",
        "session.end",
        None,
        ConfigIssue("session.end", "config.end_rule_required"),
    ),
    (
        "film.pair.rating",
        "session.end",
        {"type": "all_rounds", "params": {}},
        ConfigIssue("session.end", "config.end_rule_required"),
    ),
    (
        "film.pair.rating",
        "session.end",
        {"type": "lives", "params": {"count": 0}},
        ConfigIssue("session.end.params.count", "schema.greater_than_equal"),
    ),
    (
        "film.pair.rating",
        "source.params.metric",
        "box_office",
        ConfigIssue("source.params.metric", "schema.literal_error"),
    ),
    (
        "film.random.blackout",
        "clue.reveal",
        {"type": "audio_intro", "params": {"levels": [{"seconds": 15}]}},
        ConfigIssue("clue.reveal.type", "config.domain_unsupported"),
    ),
    (
        "film.random.blackout",
        "clue.reveal",
        {"type": "hologram", "params": {}},
        ConfigIssue("clue.reveal.type", "config.unknown_plugin"),
    ),
    (
        "film.random.blackout",
        "clue.reveal",
        {"type": "frame_progressive", "params": {"levels": []}},
        ConfigIssue("clue.reveal.params.levels", "schema.too_short"),
    ),
    (
        "film.bracket.weekly",
        "session.rounds",
        30,
        ConfigIssue("source.params.size", "config.rounds_mismatch"),
    ),
    (
        "film.bracket.weekly",
        "source.params.subject",
        {"roles": ["director"], "min_items": 10},
        ConfigIssue("source.params.subject.min_items", "config.subject_min_items_below_size"),
    ),
    (
        "film.bracket.weekly",
        "source.params.size",
        12,
        ConfigIssue("source.params.size", "schema.literal_error"),
    ),
    (
        "music.marathon.artist",
        "source.params.roles",
        ["artist", "artist"],
        ConfigIssue("source.params.roles", "schema.unique_items"),
    ),
    (
        "music.marathon.artist",
        "source.params.max_subjects",
        11,
        ConfigIssue("source.params.max_subjects", "schema.less_than_equal"),
    ),
]


@pytest.mark.parametrize(
    ("seed", "path", "value", "issue"), BROKEN, ids=[f"{b[3].code}:{b[3].path}" for b in BROKEN]
)
def test_broken_config_reports_issue(
    seed: str, path: str, value: Any, issue: ConfigIssue, seed_body: SeedBody
) -> None:
    issues = _issues(_set(seed_body(seed), path, value))
    assert issue in issues, issues


def test_every_rule_code_is_covered() -> None:
    codes = {b[3].code for b in BROKEN}
    assert codes >= {
        "config.unknown_plugin",
        "config.duplicate_partial",
        "config.partial_domain_unsupported",
        "config.domain_unsupported",
        "config.rounds_mismatch",
        "config.min_playable_below_rounds",
        "config.levels_mismatch",
        "config.points_increasing",
        "config.min_above_max",
        "config.duplicate_preset",
        "config.preset_filter_empty",
        "config.answer_format_unsupported",
        "config.single_answer_levels",
        "config.partial_unsupported",
        "config.comparison_required",
        "config.end_rule_required",
        "config.subject_min_items_below_size",
    }


# Онлайн-режимы этапа 4 (``duel``/``party``/``lobby``) собирает поток A (ContestRule, A1–A5).
STAGE3A_MODES = sorted(
    p.stem
    for p in (Path(__file__).resolve().parent / "modes").glob("*.yaml")
    if p.stem.split(".")[1] not in {"duel", "party", "lobby"}
)


# Тестовые режимы 3a и шаблон сборника 3b (источник ``collection`` — ``register_collection``).
FULL = EngineFactory(register_collection(register_builtin(PluginRegistry())))


@pytest.mark.parametrize("key", STAGE3A_MODES)
def test_stage3a_modes_are_valid_and_build(key: str, seed_body: SeedBody) -> None:
    config = parse_mode_config(seed_body(key))
    assert FULL.validate(config) == []
    engine = FULL.build(config)
    assert engine.planned_rounds() >= 1


def test_metric_must_exist_in_domain(seed_body: SeedBody) -> None:
    body = seed_body("film.pair.rating")
    body.update(key="music.pair.revenue", domain="music")
    body["clue"] = {"type": "audio_intro", "params": {"start_s": 0, "levels": [{"seconds": 15}]}}
    body["source"]["params"]["metric"] = "revenue_usd"
    assert _issues(body) == [ConfigIssue("source.params.metric", "config.metric_unsupported")]
    body["source"]["params"]["metric"] = "popularity"
    assert _issues(body) == []


def test_compatibility_is_declared_by_plugins(seed_body: SeedBody) -> None:
    """Новый источник с ``pick`` проходит без правок ядра: совместимость — атрибут плагина."""
    from gv_game_kernel.builtin import RandomSource

    class PickRandom(RandomSource):
        key = "pick_random"
        answer_formats = frozenset({"pick"})

    registry = register_builtin(PluginRegistry())
    registry.register_source(PickRandom())
    body = seed_body("film.bracket.weekly")
    body["source"] = {"type": "pick_random", "params": {"pool": {"kinds": ["film"]}}}
    assert _issues(body, EngineFactory(registry)) == []


def test_session_kind_must_be_supported(seed_body: SeedBody) -> None:
    factory = EngineFactory(
        register_builtin(PluginRegistry()), supported_sessions=frozenset({"duel"})
    )
    assert _issues(seed_body("music.daily.classic"), factory) == [
        ConfigIssue("session.kind", "config.session_unsupported")
    ]


def test_answer_format_must_be_supported(seed_body: SeedBody) -> None:
    class LiteOnlyClue:
        key = "lite_clue"
        domains = frozenset({"music"})
        params_schema = AudioIntroParams
        supported_answer_formats = frozenset({"lite"})

        def levels(self, params: Any) -> list[Any]:
            return [object()] * 6

    registry = register_builtin(PluginRegistry())
    registry.register_clue(LiteOnlyClue())  # type: ignore[arg-type]  # неполный плагин для теста
    body = _set(seed_body("music.daily.classic"), "clue.type", "lite_clue")
    assert _issues(body, EngineFactory(registry)) == [
        ConfigIssue("answer.format", "config.answer_format_unsupported")
    ]


def test_collection_source_is_not_registered_by_default(seed_body: SeedBody) -> None:
    body = _set(
        seed_body("music.random.classic"),
        "source",
        {"type": "collection", "params": {"round_options": [5, "all"], "min_items": 5}},
    )
    assert ConfigIssue("source.type", "config.unknown_plugin") in _issues(body)
    with_collection = EngineFactory(register_collection(register_builtin(PluginRegistry())))
    assert _issues(body, with_collection) == []


def test_issues_are_collected_together(seed_body: SeedBody) -> None:
    body = seed_body("music.daily.classic")
    body["domain"] = "film"
    body["source"]["params"]["size"] = 7
    body["scoring"]["params"]["points"] = [1, 2]
    body["clue"]["params"]["levels"] = [{"seconds": -1}]
    issues = _issues(body)
    paths = {i.path for i in issues}
    # Ошибка схемы загадки: перекрёстные проверки (число уровней) не выполняются.
    assert {"domain", "clue.params.levels[0].seconds", "answer.partial[0]"} <= paths
    assert "scoring.params.points" not in paths


def test_build_raises_config_error_with_all_issues(seed_body: SeedBody) -> None:
    body = _set(seed_body("music.daily.classic"), "source.params.size", 9)
    with pytest.raises(ConfigError) as info:
        FACTORY.build(parse_mode_config(body))
    assert info.value.details == {
        "errors": [{"path": "source.params.size", "code": "config.rounds_mismatch"}]
    }
