"""Контракты событий: game обрабатывает примеры потребляемых событий и подписан на их топики."""

from __future__ import annotations

from typing import Any

import pytest
from game_service.infrastructure import messaging

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
]


@pytest.mark.parametrize("event_type", CONSUMED)
def test_consumed_topic_subscribed(event_type: str) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS


async def test_consumed_examples_are_processed(game: Any) -> None:
    for event_type in CONSUMED:
        outcome = await game.deliver(load_example(event_type))
        assert outcome is Outcome.PROCESSED, event_type
