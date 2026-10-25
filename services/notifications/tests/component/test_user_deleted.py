"""identity.UserDeleted: уведомления, настройки, согласие и устройства игрока удаляются."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from notifications_service.infrastructure.db.tables import (
    broadcast_deliveries,
    broadcasts,
    devices,
    email_subscriptions,
    erased_users,
    notification_preferences,
)
from notifications_service.infrastructure.db.tables import notifications as notifications_table
from notifications_service.infrastructure.messaging import build_task_handlers
from sqlalchemy import insert

from gv_core.ids import uuid7
from gv_messaging import Outcome

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py


async def _seed_player(svc: Service, user: UUID) -> None:
    """Уведомление, настройка, согласие, устройство и доставка рассылки игрока."""
    headers = svc.auth(user)
    await svc.notify(user, new_level=2)
    put = await svc.client.put(
        "/v1/notification-settings/find_unlocked",
        json={"in_app": False, "email": False},
        headers=headers,
    )
    assert put.status_code < 300, put.text
    sub = await svc.client.put("/v1/email-subscription", json={"subscribed": True}, headers=headers)
    assert sub.status_code < 300, sub.text
    dev = await svc.client.post(
        "/v1/devices",
        json={"platform": "web", "token": f"{user.hex}-token-0123456789"},
        headers={**headers, "Idempotency-Key": str(uuid7())},
    )
    assert dev.status_code < 300, dev.text
    broadcast_id = uuid7()
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session, session.begin():
        await session.execute(
            insert(broadcasts).values(
                id=broadcast_id,
                subject="s",
                body="b",
                status="sent",
                recipients=1,
                delivered=1,
                failed=0,
                created_at=svc.clock.now(),
                created_by=uuid7(),
            )
        )
        await session.execute(
            insert(broadcast_deliveries).values(
                broadcast_id=broadcast_id,
                user_id=user,
                sent_at=svc.clock.now(),
                status="sent",
                token_hash=user.hex + user.hex,
                token_expires_at=svc.clock.now(),
            )
        )


async def _footprint(svc: Service, user: UUID) -> list[int]:
    return [
        await svc.count(table, table.c.user_id == user)
        for table in (
            notifications_table,
            notification_preferences,
            email_subscriptions,
            devices,
            broadcast_deliveries,
        )
    ]


async def test_user_deleted_erases_player_data_only(notifications: Service) -> None:
    svc = notifications
    gone, stays = uuid7(), uuid7()
    await _seed_player(svc, gone)
    await _seed_player(svc, stays)
    before = await _footprint(svc, stays)
    assert await _footprint(svc, gone) == [1, 1, 1, 1, 1]

    outcome = await svc.deliver(svc.event("identity.UserDeleted", user_id=str(gone)))

    assert outcome is Outcome.PROCESSED
    assert await _footprint(svc, gone) == [0, 0, 0, 0, 0]
    assert await _footprint(svc, stays) == before


async def test_user_deleted_is_idempotent_and_ignores_unknown_user(notifications: Service) -> None:
    svc = notifications
    gone = uuid7()
    await _seed_player(svc, gone)
    for _ in range(2):  # повторная публикация: новый event_id, то же удаление
        outcome = await svc.deliver(svc.event("identity.UserDeleted", user_id=str(gone)))
        assert outcome is Outcome.PROCESSED
    stranger = await svc.deliver(svc.event("identity.UserDeleted", user_id=str(uuid7())))
    assert stranger is Outcome.PROCESSED
    assert await _footprint(svc, gone) == [0, 0, 0, 0, 0]


async def test_deleted_player_gets_no_realtime_after_erase(notifications: Service) -> None:
    svc = notifications
    gone = uuid7()
    await _seed_player(svc, gone)
    await svc.deliver(svc.event("identity.UserDeleted", user_id=str(gone)))
    svc.users.users = []  # identity больше не отдаёт удалённого
    await svc.deliver(svc.event("progression.EventRunStarted"))
    handlers = build_task_handlers(svc.infra)
    for task in list(svc.tasks.tasks):
        await handlers[task["task_type"]](task["payload"])
    assert await _footprint(svc, gone) == [0, 0, 0, 0, 0]


async def test_user_deleted_between_page_and_batch_write_gets_no_notification(
    notifications: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Гонка: страница id прочитана, UserDeleted обработан, пачка записывается (tombstone)."""
    svc = notifications
    gone, stays = uuid7(), uuid7()
    svc.users.users = [gone, stays]
    await svc.deliver(svc.event("progression.EventRunStarted"))
    original = svc.users.active_after

    async def page_then_deleted(after: UUID | None, limit: int) -> Any:
        page = await original(after, limit)
        await svc.deliver(svc.event("identity.UserDeleted", user_id=str(gone)))
        return page

    monkeypatch.setattr(svc.users, "active_after", page_then_deleted)
    handlers = build_task_handlers(svc.infra)
    for task in list(svc.tasks.tasks):
        await handlers[task["task_type"]](task["payload"])

    assert await _footprint(svc, gone) == [0, 0, 0, 0, 0]
    assert await svc.count(notifications_table, notifications_table.c.user_id == stays) == 1


async def test_erased_users_tombstones_are_purged_after_retention(notifications: Service) -> None:
    svc = notifications
    gone = uuid7()
    await svc.deliver(svc.event("identity.UserDeleted", user_id=str(gone)))
    assert await svc.count(erased_users) == 1
    purge = svc.container.purge_erased
    assert await purge.execute() == 0  # срок ещё не вышел
    retention_days = svc.container.settings.notifications.erased_retention_days
    svc.clock.advance(timedelta(days=retention_days, seconds=1))
    assert await purge.execute() == 1
    assert await svc.count(erased_users) == 0
