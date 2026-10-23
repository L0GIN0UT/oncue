"""Контракты событий: progression публикует по схемам и обрабатывает consumed-примеры."""

from __future__ import annotations

from typing import Any

import pytest
from progression_service.infrastructure import messaging

from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

Service = Any  # фикстура `progression` описана в conftest.py

CONSUMED = [
    "game.GameFinished",
    "game.DailyCompleted",
    "match.MatchFinished",
    "rating.WeeklyTopFinalized",
    "rating.SeasonStandingsFinalized",
]
PRODUCED = [
    "progression.ExperienceGained",
    "progression.ProgressionLevelChanged",
    "progression.CurrencyCredited",
    "progression.CurrencyDebited",
]


@pytest.mark.parametrize("event_type", CONSUMED)
def test_consumed_topic_subscribed_and_handler_registered(event_type: str) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS


@pytest.mark.parametrize("event_type", PRODUCED)
def test_produced_examples_match_schema_and_topic(event_type: str) -> None:
    example = load_example(event_type)
    assert_event_matches_schema(example)
    assert example.topic == load_event_schema(event_type)["x-topic"] == "progression.progression.v1"


async def test_consumed_examples_are_processed(progression: Service) -> None:
    await progression.publish_config()
    for event_type in CONSUMED:
        outcome = await progression.deliver(load_example(event_type))
        assert outcome is Outcome.PROCESSED, event_type
    assert progression.dead_letters.letters == []


async def test_lobby_example_is_processed(progression: Service) -> None:
    await progression.publish_config()
    outcome = await progression.deliver(load_example("match.MatchFinished", variant="lobby"))
    assert outcome is Outcome.PROCESSED


async def test_published_events_follow_schemas(progression: Service) -> None:
    await progression.publish_config()
    for event_type in CONSUMED:
        await progression.deliver(load_example(event_type))
    events = await progression.outbox()
    assert {e.event_type for e in events} >= {
        "progression.ExperienceGained",
        "progression.CurrencyCredited",
    }
    for event in events:
        assert_event_matches_schema(event)
        assert event.aggregate_type == load_event_schema(event.event_type)["x-aggregate-type"]
