"""identity разбирает пример ``social.UserSanctionsChanged`` из контракта и подписан на топик."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from identity_service.application.commands.user_sanctions import ApplyUserSanctions
from identity_service.infrastructure.messaging import CONSUMED_TOPICS, USER_SANCTIONS_CHANGED

from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract


def test_topic_is_subscribed_and_example_is_valid() -> None:
    assert load_event_schema(USER_SANCTIONS_CHANGED)["x-topic"] in CONSUMED_TOPICS
    assert_event_matches_schema(load_example(USER_SANCTIONS_CHANGED))


async def test_contract_example_is_applied(world: object) -> None:
    from typing import Any

    w: Any = world
    example = load_example(USER_SANCTIONS_CHANGED)
    async with w.uow() as uow:
        result = await ApplyUserSanctions(uow.user_sanctions, uow.sessions, w.clock)(
            example.payload
        )
        await uow.commit()
    assert result.applied
    stored = next(iter(w.data.sanctions.values()))
    assert stored.version == example.payload["version"]
    assert stored.mask == example.payload["mask"]
    assert stored.mask_at(datetime(2026, 10, 24, tzinfo=UTC)) == example.payload["mask"]
