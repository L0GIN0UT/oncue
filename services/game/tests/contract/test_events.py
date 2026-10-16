"""Контракты событий: game обрабатывает примеры потребляемых событий и подписан на их топики."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from game_service.infrastructure import messaging

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

CONSUMED = [
    "catalog.MediaItemCreated",
    "catalog.MediaItemUpdated",
    "catalog.MediaItemBlocked",
    "catalog.MediaItemUnblocked",
    "media_pipeline.AssetsReady",
    "media_pipeline.AssetsFailed",
    "identity.UserDeleted",
    "collections.CollectionPublished",
    "collections.CollectionUpdated",
    "collections.CollectionDeleted",
]
# Варианты примеров той же схемы (этап 3b): набор сборника со ``scope``.
VARIANTS = [
    ("media_pipeline.AssetsReady", "collection"),
    ("media_pipeline.AssetsFailed", "collection"),
]


@pytest.mark.parametrize("event_type", CONSUMED)
def test_consumed_topic_subscribed(event_type: str) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS


async def test_consumed_examples_are_processed(game: Any) -> None:
    for event_type in CONSUMED:
        outcome = await game.deliver(load_example(event_type))
        assert outcome is Outcome.PROCESSED, event_type
    for event_type, variant in VARIANTS:
        outcome = await game.deliver(load_example(event_type, variant=variant))
        assert outcome is Outcome.PROCESSED, (event_type, variant)


async def test_collection_liked_is_skipped(game: Any) -> None:
    """Лайки game не нужны: топик тот же, тип события не обрабатывается."""
    assert load_event_schema("collections.CollectionLiked")["x-topic"] in messaging.CONSUMED_TOPICS
    assert await game.deliver(load_example("collections.CollectionLiked")) is Outcome.SKIPPED


async def test_catalog_stage3a_fields_are_projected_tolerantly(game: Any) -> None:
    """Пример ``MediaItemUpdated`` с ``clue_texts``/``countries``: метрики и персоны — в
    проекцию (плюс ``year`` метрикой), лишние поля не ломают разбор; устаревшая версия
    отбрасывается."""
    from game_service.infrastructure.db.repositories import SqlProjectionRepository

    updated = load_example("catalog.MediaItemUpdated")
    assert "clue_texts" in updated.payload and "countries" in updated.payload
    assert await game.deliver(updated) is Outcome.PROCESSED
    item_id = UUID(updated.payload["media_item_id"])
    async with game.infra.sessions() as session:
        item = await SqlProjectionRepository(session).get_item(item_id)
    assert item is not None
    assert item.metric("vote_average") == 8.4 and item.metric("year") == 2014.0
    assert item.popularity == 120.5
    assert item.person_ids == tuple(UUID(p["person_id"]) for p in updated.payload["persons"])

    stale_payload = {**updated.payload, "version": 1, "metrics": {"vote_average": 1.0}}
    stale = updated.model_copy(update={"event_id": uuid7(), "payload": stale_payload})
    assert await game.deliver(stale) is Outcome.PROCESSED
    async with game.infra.sessions() as session:
        again = await SqlProjectionRepository(session).get_item(item_id)
    assert again is not None and again.metric("vote_average") == 8.4
