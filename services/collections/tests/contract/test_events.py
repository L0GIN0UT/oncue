"""Контракты событий: collections обрабатывает consumed-примеры и публикует события по схемам."""

from __future__ import annotations

from typing import Any

import pytest
from collections_service.infrastructure import messaging

from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

CONSUMED = [
    ("catalog.MediaItemCreated", None),
    ("catalog.MediaItemUpdated", None),
    ("catalog.MediaItemBlocked", None),
    ("catalog.MediaItemUnblocked", None),
    ("game.GameFinished", None),
    ("game.GameFinished", "collection"),
    ("identity.UserRegistered", None),
    ("identity.UserProfileBasicsChanged", None),
    ("identity.UserDeleted", None),
]
PRODUCED = [
    "collections.CollectionPublished",
    "collections.CollectionUpdated",
    "collections.CollectionDeleted",
    "collections.CollectionLiked",
]


@pytest.mark.parametrize(("event_type", "variant"), CONSUMED)
def test_consumed_topic_subscribed(event_type: str, variant: str | None) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS
    assert load_example(event_type, variant=variant).event_type == event_type


async def test_consumed_examples_are_processed(svc: Any) -> None:
    for event_type, variant in CONSUMED:
        outcome = await svc.deliver(load_example(event_type, variant=variant))
        assert outcome is Outcome.PROCESSED, (event_type, variant)


async def test_duplicate_event_is_skipped_by_inbox(svc: Any) -> None:
    example = load_example("identity.UserRegistered", fresh_id=False)
    assert await svc.deliver(example) is Outcome.PROCESSED
    assert await svc.deliver(example) is Outcome.DUPLICATE


@pytest.mark.parametrize("event_type", PRODUCED)
def test_produced_examples_match_schema(event_type: str) -> None:
    assert_event_matches_schema(load_example(event_type))


async def test_every_produced_event_matches_schema(svc: Any) -> None:
    """Сценарий публикует все события топика; каждое проходит envelope и схему."""
    author, fan = svc.user(), svc.user()
    items = await svc.add_items(5)
    cid = (await svc.published(author, items=items))["collection_id"]
    await svc.client.put(f"/v1/collections/{cid}/like", headers=fan.mutate())
    await svc.client.post(
        f"/admin/v1/collections/{cid}/hide", json={"reason": "спам"}, headers=svc.admin().mutate()
    )
    await svc.block_item(items[0])
    await svc.client.delete(f"/v1/collections/{cid}", headers=author.mutate())
    envelopes = await svc.outbox()
    assert {e.event_type for e in envelopes} == set(PRODUCED)
    versions = [e.payload["version"] for e in envelopes]
    assert versions == sorted(versions) == list(range(1, len(versions) + 1))
    for envelope in envelopes:
        assert_event_matches_schema(envelope)
        assert envelope.topic == "collections.collection.v1"
        assert envelope.aggregate_id == cid
