"""Контракт события модерации UGC-изображений (ADR 0048): пример проходит схему и топик."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from media_pipeline_service.domain.ugc import UgcImage, pending_key, public_key

from gv_core.ids import uuid7
from gv_messaging.envelope import EventEnvelope
from gv_testkit import FakeClock, assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def test_asset_moderated_example_matches_schema_and_topic() -> None:
    example = load_example("media_pipeline.AssetModerated")
    assert_event_matches_schema(example)
    assert example.topic == load_event_schema("media_pipeline.AssetModerated")["x-topic"]
    assert example.topic == "media_pipeline.ugc_image.v1"


def test_events_emitted_by_domain_match_schema() -> None:
    """Реальные события агрегата (одобрение, снятие) проходят схему и топик контракта."""
    image = UgcImage.submit(
        uuid7(),
        owner_user_id=uuid7(),
        purpose="avatar",
        pending_key=pending_key("a" * 32),
        width=512,
        height=512,
        size_bytes=1000,
        sha256="0" * 64,
        now=NOW,
    )
    image.approve(moderator_id=uuid7(), new_public_key=public_key("b" * 32), now=NOW)
    image.reject(moderator_id=uuid7(), reason_code="inappropriate", now=NOW)
    clock = FakeClock(NOW)
    envelopes = [
        EventEnvelope.from_domain_event(e, producer="media_pipeline", clock=clock)
        for e in image.pull_events()
    ]
    assert [e.payload["version"] for e in envelopes] == [1, 2]
    for envelope in envelopes:
        assert envelope.topic == "media_pipeline.ugc_image.v1"
        assert_event_matches_schema(envelope)


def test_asset_moderated_has_no_free_text_reason() -> None:
    """Причина - код из конфига; свободного текста модератора в событии нет."""
    properties = load_event_schema("media_pipeline.AssetModerated")["properties"]
    assert "reason" not in properties
    assert "reason_code" in properties
