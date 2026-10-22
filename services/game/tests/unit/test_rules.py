"""Проверка конфигов и фасад правил: ошибки с путями, сиды собираются, возможности источника."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from game_service.application.rules import (
    SUPPORTED_SESSIONS,
    ModeRules,
    PeriodSchedule,
    RulesFactory,
)
from game_service.infrastructure.plugins import CatalogRegistry

from gv_game_kernel import ModeNotSoloError, PluginRegistry

pytestmark = pytest.mark.unit

SEEDS = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"


def body(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((SEEDS / f"{name}.yaml").read_text("utf-8"))
    return data


@pytest.fixture
def factory(plugin_registry: CatalogRegistry) -> RulesFactory:
    return RulesFactory(plugin_registry)


KERNEL_ONLINE = hasattr(PluginRegistry, "contest")
"""Плагины состязания и ``party_speed`` в ядре (поток A этапа 4a)."""


@pytest.mark.parametrize("name", sorted(p.stem for p in SEEDS.glob("*.yaml")))
def test_seeds_are_valid(factory: RulesFactory, name: str) -> None:
    raw = body(name)
    if raw["session"]["kind"] != "solo" and not KERNEL_ONLINE:
        pytest.skip("онлайн-сид проверяется плагинами ContestRule потока A этапа 4a")
    check = factory.check(raw)
    assert check.errors == []
    assert check.rules is not None
    # Число уровней — по параметрам загадки (pick — один, классика соло — шесть, дуэль — два).
    assert check.rules.levels_count == len(raw["clue"]["params"]["levels"])
    assert check.rules.is_online == (raw["session"]["kind"] != "solo")


def test_online_config_is_stored_but_not_played_solo(factory: RulesFactory) -> None:
    """Онлайн-конфиг game проверяет и собирает (ADR 0032), но соло-правил у него нет."""
    duel = rules_of(factory, "film.duel.frame")
    assert duel.is_online and duel.levels_count == 6 and duel.max_collections == 1
    with pytest.raises(ModeNotSoloError) as info:
        _ = duel.engine.solo_session
    assert info.value.code == "game.mode_not_solo"
    assert {"solo", "duel", "party", "lobby"} == SUPPORTED_SESSIONS
    solo = rules_of(factory, "film.collection.frame")
    assert not solo.is_online and solo.max_collections == 1


def rules_of(factory: RulesFactory, name: str) -> ModeRules:
    rules = factory.check(body(name)).rules
    assert rules is not None
    return rules


def test_schedule_and_presets_capabilities(factory: RulesFactory) -> None:
    daily = rules_of(factory, "music.daily.classic")
    random = rules_of(factory, "music.random.classic")
    assert daily.schedule == PeriodSchedule("day", "Europe/Moscow", 2, 7)
    assert daily.rounds == 5 and daily.presets() == []
    assert random.schedule is None and random.rounds == 5
    assert [p.id for p in random.presets()] == ["oldies", "modern", "rock"]
    assert daily.min_playable() == 10 and daily.round_max == 6
    assert (daily.presentation, daily.answer_format, daily.records_enabled) == (
        "score",
        "classic",
        False,
    )


def test_week_period_capabilities(factory: RulesFactory) -> None:
    """Сетка: период «неделя» из ``period_start`` ядра, тема — персоны, без ключей режима."""
    bracket = rules_of(factory, "film.bracket.weekly")
    assert bracket.schedule == PeriodSchedule("week", "Europe/Moscow", 1, 0)
    friday = datetime(2026, 10, 16, 12, tzinfo=UTC)
    assert bracket.period_start(friday) == date(2026, 10, 12)
    assert bracket.next_period_start(date(2026, 10, 12)) == date(2026, 10, 19)
    assert bracket.next_reset_at(friday) == datetime(2026, 10, 18, 21, tzinfo=UTC)
    theme = bracket.theme_query()
    assert theme is not None and theme.roles == {"director"} and theme.min_items == 32
    assert bracket.is_progressive and bracket.rounds == 31 and bracket.presentation == "bracket"
    daily = rules_of(factory, "film.daily.frame")
    assert daily.next_period_start(date(2026, 10, 10)) == date(2026, 10, 11)
    assert daily.theme_query() is None


def test_subjects_reveal_and_records_capabilities(factory: RulesFactory) -> None:
    marathon = rules_of(factory, "film.marathon.director")
    query = marathon.subject_query()
    assert query is not None and query.roles == {"director"} and marathon.max_subjects() == 3
    assert marathon.records_enabled and marathon.schedule is None
    blackout = rules_of(factory, "film.random.blackout")
    reveal = blackout.reveal_playable_for
    assert reveal is not None and reveal.clue_type == "frame_progressive"
    pair = rules_of(factory, "film.pair.rating")
    assert pair.presentation == "streak" and pair.answer_format == "pick"
    pool = pair.pool_query()
    assert pool is not None and pool.has_metric == "vote_average"
    assert rules_of(factory, "music.random.classic").subject_query() is None


def test_schema_errors_have_paths(factory: RulesFactory) -> None:
    broken = copy.deepcopy(body("music.daily.classic"))
    broken["session"]["rounds"] = "5"
    broken["unknown"] = True
    paths = {(e["path"], e["code"]) for e in factory.check(broken).errors}
    assert ("session.rounds", "schema.int_type") in paths
    assert ("unknown", "schema.extra_forbidden") in paths


def test_unknown_plugin_and_bad_params(factory: RulesFactory) -> None:
    broken = copy.deepcopy(body("music.daily.classic"))
    broken["clue"]["type"] = "no_such_clue"
    assert {"path": "clue.type", "code": "config.unknown_plugin"} in factory.check(broken).errors
    bad = copy.deepcopy(body("music.daily.classic"))
    bad["clue"]["params"]["levels"] = []
    errors = factory.check(bad).errors
    assert errors and all(e["path"].startswith("clue.params") for e in errors)


@pytest.mark.parametrize(
    "name",
    [
        "film.collection.frame",
        "film.collection.emoji",
        "music.collection.intro",
        "music.collection.emoji",
    ],
)
def test_collection_modes_are_registered_templates(factory: RulesFactory, name: str) -> None:
    """3b (D4): источник ``collection`` зарегистрирован, сиды — шаблоны без рекордов,
    варианты числа раундов и порог — из ``source.params``."""
    rules = rules_of(factory, name)
    assert rules.engine.uses_collection
    assert rules.engine.round_options() == [5, 10, 25, "all"]
    assert rules.min_playable() == 5
    assert not rules.records_enabled
    assert rules.domain == name.split(".", maxsplit=1)[0]


def test_editor_schema_lists_plugins(factory: RulesFactory) -> None:
    registry = factory.registry
    assert isinstance(registry, CatalogRegistry)
    schema = registry.editor_schema()
    assert "audio_intro" in schema["plugins"]["clue"]
    assert "daily" in schema["plugins"]["source"]
