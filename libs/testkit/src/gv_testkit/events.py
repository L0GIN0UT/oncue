"""Проверки опубликованных событий."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging.envelope import EventEnvelope
from gv_testkit.fakes import FakeEventPublisher


def published_envelopes(publisher: FakeEventPublisher) -> list[EventEnvelope]:
    """Все события, прошедшие через фейковый брокер."""
    return [EventEnvelope.from_bytes(m.value) for m in publisher.messages]


def assert_published(
    envelopes: list[EventEnvelope], event_type: str, where: Mapping[str, Any] | None = None
) -> EventEnvelope:
    """Найти ровно одно событие типа ``event_type`` с совпадающими полями payload."""
    matches = [
        e
        for e in envelopes
        if e.event_type == event_type
        and all(e.payload.get(k) == v for k, v in (where or {}).items())
    ]
    assert len(matches) == 1, (
        f"ожидалось ровно одно {event_type} с {dict(where or {})}, найдено {len(matches)}; "
        f"всего событий: {[e.event_type for e in envelopes]}"
    )
    return matches[0]


async def outbox_envelopes(session: AsyncSession, outbox: Table) -> list[EventEnvelope]:
    """События из outbox в порядке записи."""
    rows = await session.execute(
        select(outbox.c.payload).order_by(outbox.c.created_at, outbox.c.event_id)
    )
    return [EventEnvelope.from_bytes(raw) for (raw,) in rows]
