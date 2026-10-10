"""Проверка конфигов и фасад правил: ошибки с путями, сиды собираются, возможности источника."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from game_service.application.rules import RulesFactory
from game_service.infrastructure.plugins import CatalogRegistry

pytestmark = pytest.mark.unit

SEEDS = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"


def body(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((SEEDS / f"{name}.yaml").read_text("utf-8"))
    return data


@pytest.fixture
def factory(plugin_registry: CatalogRegistry) -> RulesFactory:
    return RulesFactory(plugin_registry)


@pytest.mark.parametrize("name", sorted(p.stem for p in SEEDS.glob("*.yaml")))
def test_seeds_are_valid(factory: RulesFactory, name: str) -> None:
    check = factory.check(body(name))
    assert check.errors == []
    assert check.rules is not None
    assert check.rules.levels_count == 6
    assert check.rules.round_max == 6


def test_schedule_and_presets_capabilities(factory: RulesFactory) -> None:
    daily = factory.check(body("music.daily.classic")).rules
    random = factory.check(body("music.random.classic")).rules
    assert daily is not None and random is not None
    assert daily.schedule is not None and daily.schedule.timezone == "Europe/Moscow"
    assert daily.rounds == 5 and daily.presets() == []
    assert random.schedule is None and random.rounds == 5
    assert [p.id for p in random.presets()] == ["oldies", "modern", "rock"]
    assert daily.pool is not None and daily.pool.min_playable == 10


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


def test_collection_source_is_not_registered() -> None:
    registry = CatalogRegistry()

    class Collection:
        key = "collection"

    registry.register_source(Collection())  # type: ignore[arg-type]  # минимальный фейк Source
    assert registry.source("collection") is None


def test_editor_schema_lists_plugins(factory: RulesFactory) -> None:
    registry = factory.registry
    assert isinstance(registry, CatalogRegistry)
    schema = registry.editor_schema()
    assert "audio_intro" in schema["plugins"]["clue"]
    assert "daily" in schema["plugins"]["source"]
