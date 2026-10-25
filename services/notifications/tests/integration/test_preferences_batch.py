"""Настройки пачки игроков читаются одним запросом на реальном Postgres."""

from __future__ import annotations

from typing import Any

import pytest
from notifications_service.domain.notification import TypePreference
from notifications_service.infrastructure.db.repositories import SqlUnitOfWork
from sqlalchemy import event

from gv_core.ids import uuid7

pytestmark = pytest.mark.integration

Service = Any  # фикстура `notifications` описана в conftest.py


async def test_for_users_is_one_query_and_filters_by_type(notifications: Service) -> None:
    svc = notifications
    users = [uuid7() for _ in range(4)]
    assert svc.infra.sessions is not None
    async with SqlUnitOfWork(svc.clock, sessions=svc.infra.sessions) as uow:
        await uow.preferences.save(
            users[0], "event_started", TypePreference(in_app=False, email=False), svc.clock.now()
        )
        await uow.preferences.save(
            users[1], "event_started", TypePreference(in_app=True, email=False), svc.clock.now()
        )
        await uow.preferences.save(
            users[2], "find_unlocked", TypePreference(in_app=False, email=False), svc.clock.now()
        )
        await uow.commit()

    statements: list[str] = []
    engine = svc.infra.sessions.kw["bind"].sync_engine

    def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        async with SqlUnitOfWork(svc.clock, sessions=svc.infra.sessions) as uow:
            found = await uow.preferences.for_users(users, "event_started")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert found == {
        users[0]: TypePreference(in_app=False, email=False),
        users[1]: TypePreference(in_app=True, email=False),
    }
    selects = [s for s in statements if "notification_preferences" in s]
    assert len(selects) == 1


async def test_for_users_empty_input_makes_no_query(notifications: Service) -> None:
    svc = notifications
    async with SqlUnitOfWork(svc.clock, sessions=svc.infra.sessions) as uow:
        assert await uow.preferences.for_users([], "event_started") == {}
