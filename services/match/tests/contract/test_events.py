"""Контракты событий и realtime: match обрабатывает consumed-примеры, публикует по схемам."""

from __future__ import annotations

from typing import Any

import pytest
from match_app import MatchApp
from match_service.infrastructure import messaging

from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import contracts_dir, load_event_schema

pytestmark = pytest.mark.contract

CONSUMED = [
    ("identity.UserRegistered", None),
    ("identity.UserProfileBasicsChanged", None),
    ("identity.UserDeleted", None),
    ("game.ModeConfigPublished", None),
    ("game.ModeConfigArchived", None),
]
PRODUCED = [
    ("match.MatchStarted", None),
    ("match.MatchFinished", None),
    ("match.MatchFinished", "lobby"),
    ("match.PlayerRated", None),
]


@pytest.mark.parametrize(("event_type", "variant"), CONSUMED)
def test_consumed_topic_subscribed(event_type: str, variant: str | None) -> None:
    assert load_event_schema(event_type)["x-topic"] in messaging.CONSUMED_TOPICS
    assert load_example(event_type, variant=variant).event_type == event_type


async def test_consumed_examples_are_processed(svc: MatchApp) -> None:
    for event_type, variant in CONSUMED:
        outcome = await svc.deliver(load_example(event_type, variant=variant))
        assert outcome is Outcome.PROCESSED, (event_type, variant)


async def test_duplicate_event_is_skipped_by_inbox(svc: MatchApp) -> None:
    example = load_example("identity.UserDeleted", fresh_id=False)
    assert await svc.deliver(example) is Outcome.PROCESSED
    assert await svc.deliver(example) is Outcome.DUPLICATE


@pytest.mark.parametrize(("event_type", "variant"), PRODUCED)
def test_produced_examples_match_schema(event_type: str, variant: str | None) -> None:
    assert_event_matches_schema(load_example(event_type, variant=variant))


def test_realtime_examples_cover_published_kinds() -> None:
    """Каждый вид сообщения, который публикует match, описан в контракте канала."""
    import json

    schema: dict[str, Any] = json.loads(
        (contracts_dir() / "realtime" / "room.message.v1.json").read_text(encoding="utf-8")
    )
    described = {branch["if"]["properties"]["kind"]["const"] for branch in schema["allOf"]}
    published = {
        "lobby",
        "player",
        "phase",
        "level_opened",
        "level_closed",
        "round_reveal",
        "summary",
        "chat",
        "emote",
        "closed",
    }
    assert published <= described
