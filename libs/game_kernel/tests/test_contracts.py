"""Контракт ``contracts/modes/**`` ↔ модели ``spec.py`` и сиды ``seeds/game/modes``.

Сиды проверяются и схемой (вместе с ``params`` по схеме конкретного плагина), и моделями
ядра: так автор конфига, админка (JSON Schema) и game (pydantic) видят одно и то же.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel
from referencing import Registry, Resource

from gv_game_kernel import ConfigError, ModeConfig, params_hash, parse_mode_config
from gv_game_kernel.spec import AnswerSpec, PluginSpec, SessionSpec

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
MODES = ROOT / "contracts" / "modes"
CLUES = ROOT / "contracts" / "clues"
SEEDS = sorted((ROOT / "seeds" / "game" / "modes").glob("*.yaml"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _registry() -> Registry:
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in map(_load, sorted(MODES.rglob("*.json")))
    ]
    return Registry().with_resources(resources)


def _validator(path: Path) -> Draft202012Validator:
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=_registry())


def _errors(path: Path, instance: object) -> list[str]:
    return [
        f"{path.name}:{e.json_path}: {e.message}" for e in _validator(path).iter_errors(instance)
    ]


def _plugin_schema(section: str, plugin_type: str) -> Path:
    if section == "clue":
        return CLUES / f"{plugin_type}.params.v1.json"
    return MODES / "params" / f"{section}.{plugin_type}.v1.json"


# ----------------------------------------------------------------------------- модели ↔ схема
def _assert_model_matches(model: type[BaseModel], schema: dict[str, Any], where: str) -> None:
    props = schema["properties"]
    assert set(model.model_fields) == set(props), where
    required = {name for name, f in model.model_fields.items() if f.is_required()}
    assert required == set(schema.get("required", [])), where
    assert schema.get("additionalProperties") is False, where
    assert model.model_config.get("extra") == "forbid", where


def test_mode_config_model_matches_schema() -> None:
    schema = _load(MODES / "mode_config.v1.json")
    _assert_model_matches(ModeConfig, schema, "ModeConfig")
    _assert_model_matches(SessionSpec, schema["properties"]["session"], "session")
    _assert_model_matches(AnswerSpec, schema["properties"]["answer"], "answer")
    for section in ("source", "clue", "scoring"):
        _assert_model_matches(PluginSpec, schema["properties"][section], section)
    _assert_model_matches(PluginSpec, schema["properties"]["answer"]["properties"]["matcher"], "m")


def test_schema_has_no_service_owned_fields() -> None:
    props = _load(MODES / "mode_config.v1.json")["properties"]
    assert "version" not in props
    assert "status" not in props


def test_all_mode_schemas_are_valid() -> None:
    paths = sorted(MODES.rglob("*.json"))
    assert len(paths) >= 7
    for path in paths:
        Draft202012Validator.check_schema(_load(path))


# ----------------------------------------------------------------------------- сиды
def test_four_stage2_seeds_exist() -> None:
    keys = {p.stem for p in SEEDS}
    assert keys >= {
        "music.daily.classic",
        "film.daily.frame",
        "music.random.classic",
        "film.random.frame",
    }


@pytest.mark.parametrize("seed", SEEDS, ids=lambda p: p.stem)
def test_seed_is_valid_by_schema_and_models(seed: Path) -> None:
    body = yaml.safe_load(seed.read_text(encoding="utf-8"))
    assert body["key"] == seed.stem
    errors = _errors(MODES / "mode_config.v1.json", body)
    plugins = [
        ("source", body["source"]),
        ("clue", body["clue"]),
        ("scorer", body["scoring"]),
        ("matcher", body["answer"]["matcher"]),
    ]
    for section, plugin in plugins:
        path = _plugin_schema(section, plugin["type"])
        assert path.is_file(), f"нет схемы параметров {path.relative_to(ROOT)}"
        errors += _errors(path, plugin["params"])
    assert errors == []
    config = parse_mode_config(body)
    assert config.key == seed.stem


@pytest.mark.parametrize("seed", SEEDS, ids=lambda p: p.stem)
def test_seed_clue_params_match_media_profile(seed: Path) -> None:
    """D7: params загадки сида совпадают с профилем media-pipeline — иначе нет ассетов."""
    profiles = yaml.safe_load(
        (
            ROOT / "services/media_pipeline/src/media_pipeline_service/config/asset_profiles.yaml"
        ).read_text(encoding="utf-8")
    )["profiles"]
    hashes = {(p["clue_type"], params_hash(p["params"])) for p in profiles}
    config = parse_mode_config(yaml.safe_load(seed.read_text(encoding="utf-8")))
    assert (config.clue.type, params_hash(config.clue.params)) in hashes


# ----------------------------------------------------------------------------- ошибки разбора
def _seed_body() -> dict[str, Any]:
    body: dict[str, Any] = yaml.safe_load(SEEDS[0].read_text(encoding="utf-8"))
    return body


@pytest.mark.parametrize(
    ("patch", "path", "code"),
    [
        ({"version": 3}, "version", "schema.extra_forbidden"),
        ({"status": "published"}, "status", "schema.extra_forbidden"),
        ({"key": "games.daily.x"}, "key", "schema.string_pattern_mismatch"),
        ({"domain": "anime"}, "domain", "schema.literal_error"),
    ],
)
def test_parse_errors_have_paths(patch: dict[str, Any], path: str, code: str) -> None:
    with pytest.raises(ConfigError) as info:
        parse_mode_config({**_seed_body(), **patch})
    assert info.value.code == "game.config_invalid"
    assert {"path": path, "code": code} in info.value.details["errors"]


def test_nested_paths_and_strict_types() -> None:
    body = _seed_body()
    body["session"] = {**body["session"], "rounds": "5", "allow_guest": "yes"}
    body["answer"] = {**body["answer"], "partial": ["artist", "Bad Key"]}
    with pytest.raises(ConfigError) as info:
        parse_mode_config(body)
    paths = {e["path"] for e in info.value.details["errors"]}
    assert paths == {"session.rounds", "session.allow_guest", "answer.partial[1]"}
