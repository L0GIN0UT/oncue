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
from gv_game_kernel.spec import AnswerSpec, ClueSpec, PluginSpec, SessionSpec

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
    if section in ("clue", "reveal"):
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
    for section in ("source", "scoring"):
        _assert_model_matches(PluginSpec, schema["properties"][section], section)
    _assert_model_matches(ClueSpec, schema["properties"]["clue"], "clue")
    _assert_model_matches(PluginSpec, schema["properties"]["clue"]["properties"]["reveal"], "rev")
    _assert_model_matches(PluginSpec, schema["properties"]["session"]["properties"]["end"], "end")
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
    if "end" in body["session"]:
        plugins.append(("end", body["session"]["end"]))
    if "reveal" in body["clue"]:
        plugins.append(("reveal", body["clue"]["reveal"]))
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


# ----------------------------------------------------------------------------- params плагинов
_KEYWORDS = (
    "type",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "enum",
    "uniqueItems",
    "additionalProperties",
)


def _contract_resolver() -> dict[str, dict[str, Any]]:
    schemas = [_load(p) for p in sorted([*MODES.rglob("*.json"), *CLUES.glob("*.params.v1.json")])]
    return {s["$id"]: s for s in schemas}


def _shape(schema: dict[str, Any], resolve: Any) -> dict[str, Any]:
    """Значимая для валидации форма схемы: ограничения, свойства, обязательность, items.

    Описания, заголовки и значения по умолчанию отбрасываются; ``$ref`` разворачивается;
    необязательное поле pydantic (``anyOf: [X, null]``) сводится к ``X``.
    """
    while "$ref" in schema:
        schema = resolve(schema["$ref"])
    if "anyOf" in schema:
        branches = [b for b in schema["anyOf"] if b.get("type") != "null"]
        assert len(branches) == 1, schema
        return _shape(branches[0], resolve)
    shape: dict[str, Any] = {k: schema[k] for k in _KEYWORDS if k in schema}
    if isinstance(shape.get("type"), list):
        # ``type: [integer, null]`` контракта = ``X | None`` pydantic (``anyOf`` выше).
        types = [t for t in shape["type"] if t != "null"]
        assert len(types) == 1, schema
        shape["type"] = types[0]
    if "const" in schema:
        shape["enum"] = [schema["const"]]
    if "enum" in shape:
        shape["enum"] = sorted(shape["enum"])
    if "items" in schema:
        shape["items"] = _shape(schema["items"], resolve)
    if schema.get("type") == "object" or "properties" in schema:
        shape["type"] = "object"
        shape["properties"] = {
            name: _shape(sub, resolve) for name, sub in schema.get("properties", {}).items()
        }
        shape["required"] = sorted(schema.get("required", []))
    return shape


def _model_shape(model: type[BaseModel]) -> dict[str, Any]:
    generated = model.model_json_schema(by_alias=True, mode="validation")
    defs = generated.get("$defs", {})
    return _shape(generated, lambda ref: defs[ref.rsplit("/", 1)[-1]])


def _contract_shape(path: Path) -> dict[str, Any]:
    by_id = _contract_resolver()
    return _shape(_load(path), lambda ref: by_id[ref])


def _plugin_models() -> list[tuple[str, type[BaseModel], Path]]:
    from gv_game_kernel.builtin import (
        AllRoundsParams,
        AudioBlackoutParams,
        AudioIntroParams,
        AudioReverseParams,
        BracketParams,
        ByLevelParams,
        BySpeedParams,
        DailyParams,
        DescriptionParams,
        EmojiParams,
        FrameProgressiveParams,
        FrameSequenceParams,
        LivesParams,
        MarathonParams,
        MetricCompareParams,
        NoneParams,
        PairParams,
        PoolParams,
        PosterMaskedParams,
        PreferenceParams,
        RandomParams,
        SoundtrackParams,
        StreakParams,
        TitleAliasParams,
    )

    params = MODES / "params"
    return [
        ("audio_intro", AudioIntroParams, CLUES / "audio_intro.params.v1.json"),
        ("frame_progressive", FrameProgressiveParams, CLUES / "frame_progressive.params.v1.json"),
        ("poster_masked", PosterMaskedParams, CLUES / "poster_masked.params.v1.json"),
        ("audio_reverse", AudioReverseParams, CLUES / "audio_reverse.params.v1.json"),
        ("soundtrack", SoundtrackParams, CLUES / "soundtrack.params.v1.json"),
        ("audio_blackout", AudioBlackoutParams, CLUES / "audio_blackout.params.v1.json"),
        ("frame_sequence", FrameSequenceParams, CLUES / "frame_sequence.params.v1.json"),
        ("description", DescriptionParams, CLUES / "description.params.v1.json"),
        ("emoji", EmojiParams, CLUES / "emoji.params.v1.json"),
        ("daily", DailyParams, params / "source.daily.v1.json"),
        ("random", RandomParams, params / "source.random.v1.json"),
        ("marathon", MarathonParams, params / "source.marathon.v1.json"),
        ("pair", PairParams, params / "source.pair.v1.json"),
        ("bracket", BracketParams, params / "source.bracket.v1.json"),
        ("pool", PoolParams, params / "pool.v1.json"),
        ("by_level", ByLevelParams, params / "scorer.by_level.v1.json"),
        ("by_speed", BySpeedParams, params / "scorer.by_speed.v1.json"),
        ("streak", StreakParams, params / "scorer.streak.v1.json"),
        ("none", NoneParams, params / "scorer.none.v1.json"),
        ("title_alias", TitleAliasParams, params / "matcher.title_alias.v1.json"),
        ("metric_compare", MetricCompareParams, params / "matcher.metric_compare.v1.json"),
        ("preference", PreferenceParams, params / "matcher.preference.v1.json"),
        ("all_rounds", AllRoundsParams, params / "end.all_rounds.v1.json"),
        ("lives", LivesParams, params / "end.lives.v1.json"),
    ]


@pytest.mark.parametrize(("name", "model", "path"), _plugin_models(), ids=str)
def test_plugin_params_model_matches_contract(
    name: str, model: type[BaseModel], path: Path
) -> None:
    """Модель параметров плагина = JSON Schema контракта (поля, типы, границы, items)."""
    assert _model_shape(model) == _contract_shape(path), name


CLUE_KEYS = (
    "audio_intro",
    "frame_progressive",
    "poster_masked",
    "audio_reverse",
    "soundtrack",
    "audio_blackout",
    "frame_sequence",
    "description",
    "emoji",
)
SOURCE_KEYS = ("daily", "random", "marathon", "pair", "bracket")
MATCHER_KEYS = ("title_alias", "metric_compare", "preference")
SCORER_KEYS = ("by_level", "by_speed", "streak", "none")
END_KEYS = ("all_rounds", "lives")


def test_every_registered_plugin_has_a_contract() -> None:
    from gv_game_kernel import PluginRegistry, register_builtin

    registry = register_builtin(PluginRegistry())
    covered = {model for _, model, _ in _plugin_models()}
    plugins = [
        *(registry.clue(k) for k in CLUE_KEYS),
        *(registry.source(k) for k in SOURCE_KEYS),
        *(registry.matcher(k) for k in MATCHER_KEYS),
        *(registry.scorer(k) for k in SCORER_KEYS),
        *(registry.end_rule(k) for k in END_KEYS),
    ]
    missing = [p for p in plugins if p is None or p.params_schema not in covered]
    assert missing == []
    # Каждой схеме загадки и параметров режима соответствует зарегистрированный плагин.
    clue_files = {p.name.removesuffix(".params.v1.json") for p in CLUES.glob("*.params.v1.json")}
    assert clue_files == set(CLUE_KEYS)
    sections = {"source": SOURCE_KEYS, "matcher": MATCHER_KEYS, "scorer": SCORER_KEYS}
    sections["end"] = END_KEYS
    for section, keys in sections.items():
        files = {p.name.split(".")[1] for p in (MODES / "params").glob(f"{section}.*.v1.json")}
        assert files == set(keys), section


def test_answer_formats_are_registered() -> None:
    from gv_game_kernel import PluginRegistry, register_builtin

    registry = register_builtin(PluginRegistry())
    pick, classic = registry.answer_format("pick"), registry.answer_format("classic")
    assert pick is not None and pick.input_kinds == frozenset({"option"}) and pick.needs_options
    assert classic is not None and classic.input_kinds == frozenset({"item", "text"})


def test_golden_profile_params_parse_by_clue_models() -> None:
    """Параметры профилей media-pipeline из golden ``params_hash`` проходят модель загадки."""
    from gv_game_kernel import PluginRegistry, register_builtin

    registry = register_builtin(PluginRegistry())
    vectors = _load(CLUES / "params_hash.golden.json")["vectors"]
    profiles = [v for v in vectors if v["name"].startswith("profile:")]
    assert {v["name"].split(":")[1] for v in profiles} == set(CLUE_KEYS)
    for vector in profiles:
        clue = registry.clue(vector["name"].split(":")[1])
        assert clue is not None
        clue.params_schema.model_validate(vector["params"])
        assert params_hash(vector["params"]) == vector["params_hash"], vector["name"]


TEST_MODES = sorted((Path(__file__).parent / "modes").glob("*.yaml"))


@pytest.mark.parametrize("path", TEST_MODES, ids=lambda p: p.stem)
def test_stage3a_test_modes_are_valid_by_schema(path: Path) -> None:
    """Тестовые конфиги режимов 3a валидны по JSON Schema (как сиды game)."""
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = _errors(MODES / "mode_config.v1.json", body)
    errors += _errors(_plugin_schema("source", body["source"]["type"]), body["source"]["params"])
    errors += _errors(_plugin_schema("clue", body["clue"]["type"]), body["clue"]["params"])
    errors += _errors(_plugin_schema("scorer", body["scoring"]["type"]), body["scoring"]["params"])
    matcher = body["answer"]["matcher"]
    errors += _errors(_plugin_schema("matcher", matcher["type"]), matcher["params"])
    if "end" in body["session"]:
        end = body["session"]["end"]
        errors += _errors(_plugin_schema("end", end["type"]), end["params"])
    if "reveal" in body["clue"]:
        reveal = body["clue"]["reveal"]
        errors += _errors(_plugin_schema("reveal", reveal["type"]), reveal["params"])
    assert errors == []
