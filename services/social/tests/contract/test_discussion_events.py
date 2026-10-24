"""События комментариев и жалоб соответствуют схемам; входящие события - примерам контрактов."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from social_service.domain.discussion_events import (
    CommentDeleted,
    CommentPosted,
    ContentModerated,
    ReportFiled,
    ReportResolved,
)
from social_service.infrastructure.messaging import CONSUMED_TOPICS
from social_service.infrastructure.messaging.moderation import _HANDLERS

from gv_core.domain import DomainEvent
from gv_core.ids import uuid7
from gv_messaging import EventEnvelope
from gv_testkit import FakeClock, assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


def _events() -> list[DomainEvent]:
    cid, uid, rid = uuid7(), uuid7(), uuid7()
    return [
        CommentPosted(
            aggregate_id=str(cid),
            at=NOW,
            author_id=uid,
            author_nickname="Alice",
            collection_author_id=uuid7(),
            collection_id=uuid7(),
            collection_title="Хиты",
            comment_id=cid,
            notify_owner=True,
        ),
        CommentDeleted(
            aggregate_id=str(cid),
            at=NOW,
            by_role="account_deleted",
            collection_id=uuid7(),
            comment_id=cid,
            deleted_by=None,
        ),
        ReportFiled(
            aggregate_id=str(rid),
            at=NOW,
            author_id=uid,
            case_id=None,
            context_match_id=None,
            reason_code="spam",
            report_id=rid,
            target_id=uuid7(),
            target_kind="collection",
            target_user_id=uuid7(),
        ),
        ContentModerated(
            aggregate_id=str(cid),
            at=NOW,
            case_id=cid,
            decision="content_removed",
            moderator_id=uid,
            target_id=uuid7(),
            target_kind="avatar",
            target_user_id=uuid7(),
        ),
        ReportResolved(
            aggregate_id=str(rid),
            at=NOW,
            case_id=cid,
            decision="dismissed",
            report_id=rid,
            reporter_id=uid,
        ),
    ]


@pytest.mark.parametrize("event", _events(), ids=lambda e: type(e).__name__)
def test_produced_event_matches_schema_and_topic(event: DomainEvent) -> None:
    envelope = EventEnvelope.from_domain_event(event, producer="social", clock=FakeClock(NOW))
    assert_event_matches_schema(envelope)
    assert envelope.topic == load_event_schema(envelope.event_type)["x-topic"] == "social.social.v1"


def test_report_event_has_no_free_text_fields() -> None:
    for event in _events():
        assert not {"text", "body"} & set(event.payload())


@pytest.mark.parametrize("event_type", sorted(_HANDLERS))
def test_consumed_examples_are_valid_and_topics_are_subscribed(event_type: str) -> None:
    assert_event_matches_schema(load_example(event_type))
    assert load_event_schema(event_type)["x-topic"] in CONSUMED_TOPICS
