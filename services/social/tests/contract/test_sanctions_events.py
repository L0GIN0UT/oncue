"""Событие UserSanctionsChanged соответствует схеме и примеру контракта."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from social_service.domain.sanctions import ActiveSanction, UserSanctionsChanged

from gv_messaging import EventEnvelope
from gv_testkit import FakeClock, assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
USER = UUID(int=2)


def _event(*, changed_by: UUID | None, expires: datetime | None) -> UserSanctionsChanged:
    return UserSanctionsChanged(
        aggregate_id=str(USER),
        account_banned=True,
        active=[ActiveSanction(expires_at=expires, kind="account", sanction_id=UUID(int=7))],
        at=NOW,
        changed_by=changed_by,
        mask=0,
        next_expiry_at=expires,
        user_id=USER,
        version=4,
    )


@pytest.mark.parametrize(
    ("changed_by", "expires"), [(UUID(int=1), NOW), (None, None)], ids=["moderator", "expiry"]
)
def test_event_matches_schema(changed_by: UUID | None, expires: datetime | None) -> None:
    envelope = EventEnvelope.from_domain_event(
        _event(changed_by=changed_by, expires=expires), producer="social", clock=FakeClock(NOW)
    )
    assert_event_matches_schema(envelope)
    assert envelope.topic == load_event_schema(envelope.event_type)["x-topic"] == "social.social.v1"


def test_event_has_no_reason() -> None:
    payload = _event(changed_by=None, expires=None).payload()
    assert not {"reason", "note", "text"} & set(payload)
    assert not any("reason" in a for a in payload["active"])


def test_example_is_valid() -> None:
    assert_event_matches_schema(load_example("social.UserSanctionsChanged"))
