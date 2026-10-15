"""Совместимость с контрактами: профили и модели параметров ↔ ``contracts/clues``,
потребление примеров ``catalog.*`` и публикация ``media_pipeline.*``."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from media_pipeline_service.application.generators import GeneratorRegistry
from media_pipeline_service.domain.clues import ClueParams
from media_pipeline_service.infrastructure.config import read_profiles_document
from media_pipeline_service.infrastructure.container import MediaContainer
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.infrastructure.messaging import (
    CONSUMED_TOPICS,
    ITEM_BLOCKED,
    ITEM_CREATED,
    ITEM_UNBLOCKED,
    ITEM_UPDATED,
    _SnapshotPayload,
    _StatusPayload,
)
from media_pipeline_service.settings import Settings
from pydantic import ValidationError

from gv_service import Infrastructure
from gv_testkit import load_example
from gv_testkit.contracts import contracts_dir, load_event_schema

pytestmark = pytest.mark.contract


def registry() -> GeneratorRegistry:
    return MediaContainer(Infrastructure(settings=Settings(), tables=TABLES)).registry


def clue_schema(clue_type: str) -> Draft202012Validator:
    path = contracts_dir() / "clues" / f"{clue_type}.params.v1.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_every_generator_has_clue_contract() -> None:
    for clue_type in registry().clue_types:
        assert (contracts_dir() / "clues" / f"{clue_type}.params.v1.json").is_file(), clue_type


def test_builtin_profiles_match_clue_contracts() -> None:
    document = read_profiles_document()
    catalog = registry().build_catalog(document)
    assert len(catalog) == len(document["profiles"])
    for entry in document["profiles"]:
        errors = [e.message for e in clue_schema(entry["clue_type"]).iter_errors(entry["params"])]
        assert not errors, (entry["clue_type"], errors)
    for profile in catalog:  # нормализованные параметры тоже валидны по контракту
        assert not list(clue_schema(profile.clue_type).iter_errors(profile.params_json()))


def test_builtin_profiles_match_golden_params_hash() -> None:
    """Профили ``profile:<clue>:<profile>`` из golden-файла есть в конфиге с тем же хэшем:
    по этим хэшам game (сиды режимов) находит ассеты."""
    golden = json.loads((contracts_dir() / "clues" / "params_hash.golden.json").read_text())
    expected = {
        v["name"].removeprefix("profile:"): v["params_hash"]
        for v in golden["vectors"]
        if v["name"].startswith("profile:")
    }
    catalog = registry().build_catalog(read_profiles_document())
    actual = {f"{p.clue_type}:{p.profile_key}": p.params_hash for p in catalog}
    assert actual == expected


def test_every_new_clue_type_has_default_profile() -> None:
    catalog = registry().build_catalog(read_profiles_document())
    with_default = {p.clue_type for p in catalog if p.profile_key == "default"}
    assert with_default == registry().clue_types


# Одноуровневые примеры: монотонность (правило домена сверх контракта) не участвует.
_AUDIO_SAMPLES: list[Any] = [
    {"levels": [{"seconds": 1}]},
    {"levels": [{"seconds": 0.5}], "start_s": 30},
    {"levels": [{"seconds": 60.01}]},
    {"levels": [{"seconds": 0}]},
    {"levels": [{"seconds": 1}], "start_s": -1},
    {"levels": [{"seconds": 1}] * 21},
    {"levels": [], "start_s": 0},
    {"levels": [{"seconds": 1}], "x": 1},
]
SAMPLES: dict[str, list[Any]] = {
    "audio_reverse": _AUDIO_SAMPLES,
    "soundtrack": _AUDIO_SAMPLES,
    "audio_blackout": _AUDIO_SAMPLES,
    "frame_sequence": [
        {"levels": [{"frame": 1, "max_width": 960}]},
        {"levels": [{"frame": 6, "max_width": 64}]},
        {"levels": [{"frame": 0, "max_width": 960}]},
        {"levels": [{"frame": 7, "max_width": 960}]},
        {"levels": [{"frame": 1, "max_width": 63}]},
        {"levels": [{"frame": 1}]},
        {"levels": [{"frame": 1.5, "max_width": 960}]},
        {"levels": [{"frame": 1, "max_width": 960, "blur_px": 1}]},
        {"levels": []},
    ],
    "description": [
        {"levels": [{"fields": ["plot_masked"]}]},
        {"levels": [{"fields": ["year", "genres", "country", "director", "cast_top3"]}]},
        {"levels": [{"fields": ["tagline"]}]},
        {"levels": [{"fields": []}]},
        {"levels": [{"fields": ["title"]}]},
        {"levels": [{"fields": ["year", "year"]}]},
        {"levels": [{"fields": "year"}]},
        {"levels": [{"fields": ["year"]}] * 11},
        {"levels": [{"fields": ["year"], "x": 1}]},
    ],
    "emoji": [
        {"levels": [{"count": 1}]},
        {"levels": [{"count": 12}]},
        {"levels": [{"count": None}]},
        {"levels": [{"count": 0}]},
        {"levels": [{"count": 13}]},
        {"levels": [{"count": 1.5}]},
        {"levels": [{}]},
        {"levels": [{"count": "2"}]},
        {"levels": []},
    ],
    "audio_intro": [
        {"levels": [{"seconds": 1}]},
        {"levels": [{"seconds": 0.5}], "start_s": 12.5},
        {"levels": [{"seconds": 60}]},
        {"levels": [{"seconds": 60.01}]},
        {"levels": [{"seconds": 0}]},
        {"levels": [{"seconds": "1"}]},
        {"levels": [{"seconds": True}]},
        {"levels": []},
        {"levels": [{"seconds": 1}] * 21},
        {"levels": [{"seconds": 1}], "start_s": -1},
        {"levels": [{"seconds": 1, "x": 1}]},
        {"levels": [{"seconds": 1}], "extra": 1},
        {},
    ],
    "frame_progressive": [
        {"levels": [{"blur_px": 0, "crop": 1, "max_width": 64}]},
        {"levels": [{"blur_px": 200, "crop": 0.01, "max_width": 4096}]},
        {"levels": [{"blur_px": 201, "crop": 1, "max_width": 640}]},
        {"levels": [{"blur_px": 1, "crop": 0, "max_width": 640}]},
        {"levels": [{"blur_px": 1, "crop": 1.5, "max_width": 640}]},
        {"levels": [{"blur_px": 1, "crop": 1, "max_width": 640.5}]},
        {"levels": [{"blur_px": 1, "crop": 1, "max_width": 63}]},
        {"levels": [{"blur_px": 1, "crop": 1}]},
    ],
    "poster_masked": [
        {"levels": [{"pixel_block": 1}]},
        {"levels": [{"pixel_block": 256}], "mask_regions": [{"x": 0, "y": 0.8, "w": 1, "h": 0.2}]},
        {"levels": [{"pixel_block": 0}]},
        {"levels": [{"pixel_block": 2.5}]},
        {"levels": [{"pixel_block": 8}], "mask_regions": [{"x": 0, "y": 0, "w": 0, "h": 1}]},
        {"levels": [{"pixel_block": 8}], "mask_regions": [{"x": 1.1, "y": 0, "w": 1, "h": 1}]},
        {"levels": [{"pixel_block": 8}], "mask_regions": [{"x": 0, "y": 0, "w": 1}]},
    ],
}


@pytest.mark.parametrize(
    ("clue_type", "sample"),
    [(clue, sample) for clue, samples in SAMPLES.items() for sample in samples],
)
def test_params_models_agree_with_json_schema(clue_type: str, sample: Any) -> None:
    generator = registry().get(clue_type)
    assert generator is not None
    model: type[ClueParams] = generator.params_model
    schema_ok = clue_schema(clue_type).is_valid(sample)
    try:
        model.from_mapping(sample)
        model_ok = True
    except (ValidationError, ValueError):
        model_ok = False
    assert model_ok == schema_ok, (sample, schema_ok, model_ok)


# ---------------------------------------------------------------- события
def test_consumes_catalog_topic_and_examples() -> None:
    assert CONSUMED_TOPICS == ("catalog.media_item.v1",)
    for event_type in (ITEM_CREATED, ITEM_UPDATED):
        envelope = load_example(event_type)
        assert envelope.topic in CONSUMED_TOPICS
        parsed = _SnapshotPayload.model_validate(envelope.payload)
        assert parsed.version == envelope.payload["version"]
    for event_type in (ITEM_BLOCKED, ITEM_UNBLOCKED):
        envelope = load_example(event_type)
        assert envelope.topic in CONSUMED_TOPICS
        _StatusPayload.model_validate(envelope.payload)


def test_snapshot_parser_covers_schema_enums() -> None:
    schema = load_event_schema(ITEM_CREATED)
    props = schema["properties"]
    source_props = props["source_media"]["items"]["properties"]
    for kind in source_props["kind"]["enum"]:
        for origin in source_props["origin"]["enum"]:
            for status in props["status"]["enum"]:
                payload = {
                    **load_example(ITEM_CREATED).payload,
                    "status": status,
                    "source_media": [
                        {
                            "source_media_ref": "0192a3b4-c5d6-7e8f-9a0b-000000000300",
                            "kind": kind,
                            "origin": origin,
                            "uri": "https://image.tmdb.org/x.jpg",
                        }
                    ],
                }
                _SnapshotPayload.model_validate(payload)
    for domain in props["domain"]["enum"]:
        _SnapshotPayload.model_validate({**load_example(ITEM_CREATED).payload, "domain": domain})


def test_unknown_source_kinds_are_skipped_not_rejected() -> None:
    """Виды исходников, которые catalog начнёт публиковать раньше, чем media их научится
    обрабатывать, пропускаются; новые поля этапа 3a (position, source_url, license_note)
    разбираются."""
    from media_pipeline_service.infrastructure.messaging import known_sources

    payload = {
        **load_example(ITEM_CREATED).payload,
        "source_media": [
            {
                "source_media_ref": f"0192a3b4-c5d6-7e8f-9a0b-00000000030{i}",
                "kind": kind,
                "origin": "upload",
                "uri": "https://image.tmdb.org/x.jpg",
                "position": i + 1,
                "source_url": "https://example.org/a",
                "license_note": "CC BY 4.0",
            }
            for i, kind in enumerate(("frame", "soundtrack", "audio_clip", "hologram"))
        ],
    }
    parsed = _SnapshotPayload.model_validate(payload)
    sources = known_sources(parsed.source_media)
    assert [s.kind for s in sources] == ["frame", "soundtrack", "audio_clip"]
    assert [s.position for s in sources] == [1, 2, 3]
    assert {(s.source_url, s.license_note) for s in sources} == {
        ("https://example.org/a", "CC BY 4.0")
    }


def test_produced_event_fields_are_declared() -> None:
    from dataclasses import fields

    from media_pipeline_service.domain.asset_set import AssetsFailed, AssetsReady

    for event_cls, name in ((AssetsReady, "AssetsReady"), (AssetsFailed, "AssetsFailed")):
        schema = load_event_schema(f"media_pipeline.{name}")
        declared = {f.name for f in fields(event_cls)} - {"aggregate_id"}
        assert declared == set(schema["required"]) == set(schema["properties"])
        assert event_cls.aggregate_type == schema["x-aggregate-type"]


# ---------------------------------------------------------------- JSON-ассеты текстов (ADR 0024)
async def test_text_assets_match_text_asset_contract() -> None:
    from media_pipeline_service.application.generators import DescriptionGenerator, EmojiGenerator
    from media_pipeline_service.domain.texts import text_content

    schema = json.loads((contracts_dir() / "clues" / "text_asset.v1.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    content = text_content(
        clue_texts={"plot_masked": "Сюжет " * 500, "tagline": "Слоган", "emoji": "🚀🌌⏳🇷🇺🎬🧑‍🚀🕳️"},
        year=2014,
        genres=["фантастика"] * 200,
        countries=["US"],
        directors=["Режиссёр"],
        cast=["А", "Б", "В"],
    )
    source = [json.dumps(content).encode()]
    catalog = registry().build_catalog(read_profiles_document())
    for generator, clue_type in (
        (DescriptionGenerator(), "description"),
        (EmojiGenerator(), "emoji"),
    ):
        for profile in catalog:
            if profile.clue_type != clue_type:
                continue
            for asset in await generator.generate(source, profile.params):
                errors = [e.message for e in validator.iter_errors(json.loads(asset.data))]
                assert not errors, (clue_type, profile.profile_key, errors)
