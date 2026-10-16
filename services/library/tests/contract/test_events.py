"""Контракты событий: library обрабатывает примеры потребляемых событий, а публикуемые
``library.ItemSaved|ItemRemoved`` проходят схемы и уходят в объявленный топик."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from library_service.domain.events import ItemRemoved, ItemSaved
from library_service.domain.shelves import RemoveSource, SaveSource
from library_service.infrastructure import messaging

from gv_core.clock import SystemClock
from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, Outcome
from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

CONSUMED = [
    "catalog.MediaItemCreated",
    "catalog.MediaItemUpdated",
    "catalog.MediaItemBlocked",
    "catalog.MediaItemUnblocked",
    "identity.UserDeleted",
]


@pytest.mark.parametrize("event_type", CONSUMED)
def test_consumed_topic_subscribed(event_type: str) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS


async def test_consumed_examples_are_processed(library: Any) -> None:
    for event_type in CONSUMED:
        outcome = await library.deliver(load_example(event_type))
        assert outcome is Outcome.PROCESSED, event_type


async def test_catalog_projection_is_ordered_by_version(library: Any) -> None:
    """Устаревший снимок и устаревший статус отбрасываются; лишние поля не ломают разбор."""
    _, me = library.user()
    updated = load_example("catalog.MediaItemUpdated")
    assert await library.deliver(updated) is Outcome.PROCESSED
    item_id = UUID(updated.payload["media_item_id"])
    version = updated.payload["version"]
    stale = updated.model_copy(
        update={"event_id": uuid7(), "payload": {**updated.payload, "version": 1, "title": "Old"}}
    )
    assert await library.deliver(stale) is Outcome.PROCESSED
    await library.set_blocked(item_id, blocked=True, version=version)  # не новее — отброшено
    assert (await library.save(me, "system:favorite", item_id)).status_code == 200
    page = (await library.client.get("/v1/shelves/system:favorite/items", headers=me)).json()
    assert page["items"][0]["title"] == updated.payload["title"]
    assert page["items"][0]["status"] == "available"


def test_produced_examples_match_schema() -> None:
    for event_type in ("library.ItemSaved", "library.ItemRemoved"):
        example = load_example(event_type)
        assert_event_matches_schema(example)
        assert example.topic == load_event_schema(event_type)["x-topic"]


@pytest.mark.parametrize(
    ("cls", "source", "kind", "key"),
    [
        *((ItemSaved, s.value, "system", "watched") for s in SaveSource),
        *((ItemRemoved, s.value, "custom", None) for s in RemoveSource),
    ],
)
def test_domain_events_match_schema(cls: Any, source: str, kind: str, key: str | None) -> None:
    clock = SystemClock()
    user_id = uuid7()
    event = cls(
        aggregate_id=str(user_id),
        user_id=user_id,
        shelf_id=uuid7(),
        shelf_kind=kind,
        shelf_key=key,
        media_item_id=uuid7(),
        domain="film",
        source=source,
        version=3,
        at=clock.now(),
    )
    envelope = EventEnvelope.from_domain_event(event, producer="library", clock=clock)
    assert_event_matches_schema(envelope)
    assert envelope.topic == load_event_schema(envelope.event_type)["x-topic"]
    assert envelope.aggregate_id == str(user_id)
