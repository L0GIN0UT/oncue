"""Уведомления сезонных событий 5d: находка, завершение, старт для аудитории (ADR 0054-0056)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from notifications_service.application.audience import FANOUT_TASK
from notifications_service.infrastructure.db.repositories import SqlNotifications
from notifications_service.infrastructure.db.tables import finished_event_runs
from notifications_service.infrastructure.db.tables import notifications as notifications_table
from notifications_service.infrastructure.messaging import build_task_handlers

from gv_core.ids import uuid7
from gv_testkit import assert_realtime_message_matches_schema

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py


async def test_find_unlocked_says_found_of_total_with_event_link(notifications: Service) -> None:
    svc = notifications
    user = uuid7()
    await svc.deliver(svc.event("progression.FindUnlocked", user_id=str(user), found=2, total=3))
    [row] = await svc.rows(notifications_table)
    assert (row.type, row.data) == ("find_unlocked", {"event_key": "new_year_2026"})
    assert "найдено 2 из 3" in row.body
    [(_, message)] = svc.realtime.published
    assert_realtime_message_matches_schema(message, "notification.created")


async def test_find_unlocked_repeat_with_new_event_id_is_deduplicated(
    notifications: Service,
) -> None:
    svc = notifications
    user = uuid7()
    for _ in range(2):
        await svc.deliver(svc.event("progression.FindUnlocked", user_id=str(user)))
    assert await svc.count(notifications_table) == 1


async def test_event_completed_names_the_event(notifications: Service) -> None:
    svc = notifications
    user = uuid7()
    await svc.deliver(svc.event("progression.EventCompleted", user_id=str(user)))
    [row] = await svc.rows(notifications_table)
    assert row.type == "event_completed"
    assert "«" in row.body
    assert row.data == {"event_key": "new_year_2026"}


async def _mute(svc: Service, user: UUID, type_key: str) -> None:
    response = await svc.client.put(
        f"/v1/notification-settings/{type_key}",
        json={"in_app": False, "email": False},
        headers=svc.auth(user),
    )
    assert response.status_code < 300, response.text


async def test_muted_type_is_not_delivered(notifications: Service) -> None:
    svc = notifications
    user = uuid7()
    await _mute(svc, user, "find_unlocked")
    await svc.deliver(svc.event("progression.FindUnlocked", user_id=str(user)))
    assert await svc.count(notifications_table) == 0


async def _run_fanout(svc: Service) -> None:
    handlers = build_task_handlers(svc.infra)
    for task in list(svc.tasks.tasks):
        await handlers[task["task_type"]](task["payload"])
    await svc.container.dispatcher.drain()


async def test_event_started_fans_out_in_batches_idempotently(notifications: Service) -> None:
    svc = notifications
    svc.container._cfg.fanout_batch_size = 2
    users: list[UUID] = [uuid7() for _ in range(5)]
    for user in users:
        await svc.notify(user, new_level=2)
    svc.realtime.published.clear()

    await svc.deliver(svc.event("progression.EventRunStarted"))
    assert [t["task_type"] for t in svc.tasks.tasks] == [FANOUT_TASK]
    await _run_fanout(svc)
    await _run_fanout(svc)  # повтор задачи - без дублей

    started = await svc.rows(notifications_table, notifications_table.c.type == "event_started")
    assert sorted(r.user_id for r in started) == sorted(users)
    assert all(r.data == {"event_key": "new_year_2026"} for r in started)
    assert len(svc.realtime.published) == len(users)


async def test_event_started_skips_players_who_muted_it(notifications: Service) -> None:
    svc = notifications
    muted, other = uuid7(), uuid7()
    await svc.notify(other, new_level=2)
    await _mute(svc, muted, "event_started")
    await svc.deliver(svc.event("progression.EventRunStarted"))
    await _run_fanout(svc)
    started = await svc.rows(notifications_table, notifications_table.c.type == "event_started")
    assert [r.user_id for r in started] == [other]


# ---------------------------------------------------------------- остановка рассылки (ADR 0054)
OTHER_RUN = "0192a3b4-c5d6-7e8f-9a0b-0000000e0099"


async def _finish(svc: Service, reason: str, run_id: str | None = None) -> Any:
    extra = {"run_id": run_id} if run_id else {}
    return await svc.deliver(svc.event("progression.EventRunFinished", reason=reason, **extra))


async def _fan_out_stopped_after_first_batch(svc: Service, reason: str) -> None:
    """Первая пачка из 2 игроков доставлена, затем приходит EventRunFinished, вторая выходит."""
    svc.container._cfg.fanout_batch_size = 2
    for _ in range(5):
        await svc.notify(uuid7(), new_level=2)
    await svc.deliver(svc.event("progression.EventRunStarted"))

    original = SqlNotifications.known_users_after
    calls = 0

    async def after_first_read(self: Any, after: Any, limit: int) -> Any:
        nonlocal calls
        users = await original(self, after, limit)
        calls += 1
        if calls == 1:
            await _finish(svc, reason)
        return users

    SqlNotifications.known_users_after = after_first_read  # type: ignore[method-assign]
    try:
        await _run_fanout(svc)
    finally:
        SqlNotifications.known_users_after = original  # type: ignore[method-assign]
    assert calls == 1, "вторая пачка должна выйти до чтения аудитории"


@pytest.mark.parametrize("reason", ["stopped", "ended"])
async def test_finished_run_stops_fanout_but_keeps_delivered(
    notifications: Service, reason: str
) -> None:
    svc = notifications
    await _fan_out_stopped_after_first_batch(svc, reason)
    started = await svc.rows(notifications_table, notifications_table.c.type == "event_started")
    assert len(started) == 2


async def test_repeated_run_finished_is_idempotent(notifications: Service) -> None:
    svc = notifications
    await _finish(svc, "stopped")
    await _finish(svc, "ended")
    [row] = await svc.rows(finished_event_runs)
    assert row.reason == "stopped"


async def test_other_run_fanout_is_not_affected(notifications: Service) -> None:
    svc = notifications
    users = [uuid7() for _ in range(3)]
    for user in users:
        await svc.notify(user, new_level=2)
    await _finish(svc, "stopped")  # запуск из примера
    await svc.deliver(svc.event("progression.EventRunStarted", run_id=OTHER_RUN))
    await _run_fanout(svc)
    started = await svc.rows(notifications_table, notifications_table.c.type == "event_started")
    assert sorted(r.user_id for r in started) == sorted(users)
