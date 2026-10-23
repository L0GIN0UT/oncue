"""Контракты событий: rating публикует по схемам и обрабатывает consumed-примеры."""

from __future__ import annotations

from typing import Any

import pytest
from rating_service.infrastructure import messaging

from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

RatingService = Any  # фикстура `rating` описана в conftest.py

CONSUMED = [
    "match.MatchFinished",
    "identity.UserRegistered",
    "identity.UserProfileBasicsChanged",
    "identity.UserDeleted",
]
PRODUCED = [
    ("rating.RatingChanged", None),
    ("rating.RatingChanged", "calibrating"),
    ("rating.LadderPublished", None),
    ("rating.SeasonStarted", None),
    ("rating.SeasonFinished", None),
    ("rating.WeeklyTopFinalized", None),
]


@pytest.mark.parametrize("event_type", CONSUMED)
def test_consumed_topic_subscribed(event_type: str) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS


async def test_consumed_examples_are_processed(rating: RatingService) -> None:
    await rating.seed()
    for event_type in CONSUMED:
        outcome = await rating.deliver(load_example(event_type))
        assert outcome is Outcome.PROCESSED, event_type


@pytest.mark.parametrize(("event_type", "variant"), PRODUCED)
def test_produced_examples_match_schema(event_type: str, variant: str | None) -> None:
    example = load_example(event_type, variant=variant)
    assert_event_matches_schema(example)
    assert example.topic == load_event_schema(event_type)["x-topic"] == "rating.rating.v1"
    assert example.aggregate_type == load_event_schema(event_type)["x-aggregate-type"]


async def test_published_events_use_contract_topic_and_aggregate(rating: RatingService) -> None:
    import uuid

    await rating.seed(calibration__matches=1)
    await rating.play(uuid.uuid4(), uuid.uuid4())
    events = await rating.outbox()
    assert {e.event_type for e in events} >= {
        "rating.LadderPublished",
        "rating.SeasonStarted",
        "rating.RatingChanged",
    }
    for envelope in events:
        assert_event_matches_schema(envelope)
        assert envelope.topic == load_event_schema(envelope.event_type)["x-topic"]
        assert envelope.aggregate_type == load_event_schema(envelope.event_type)["x-aggregate-type"]
    changed = next(e for e in events if e.event_type == "rating.RatingChanged")
    assert changed.aggregate_id == changed.payload["user_id"]
