"""Контракт события модерации UGC-изображений (ADR 0048): пример проходит схему и топик."""

from __future__ import annotations

import pytest

from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract


def test_asset_moderated_example_matches_schema_and_topic() -> None:
    example = load_example("media_pipeline.AssetModerated")
    assert_event_matches_schema(example)
    assert example.topic == load_event_schema("media_pipeline.AssetModerated")["x-topic"]
    assert example.topic == "media_pipeline.ugc_image.v1"


def test_asset_moderated_has_no_free_text_reason() -> None:
    """Причина - код из конфига; свободного текста модератора в событии нет."""
    properties = load_event_schema("media_pipeline.AssetModerated")["properties"]
    assert "reason" not in properties
    assert "reason_code" in properties
