"""Admin-выключатели типов уведомлений: API, аудит, влияние на создание уведомлений."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from notifications_service.application.broadcasts import BROADCAST_TASK
from notifications_service.infrastructure.db.tables import (
    admin_actions,
    notification_preferences,
    notification_type_flags,
)
from notifications_service.infrastructure.db.tables import notifications as notifications_table
from notifications_service.infrastructure.messaging import build_task_handlers

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py
ADMIN = uuid7()
PERM = "notifications.types.manage"
REASON = {"reason": "тип шумит, отключаем до разбора"}


def put_url(type_key: str) -> str:
    return f"/admin/v1/notification-types/{type_key}"


async def put(
    svc: Service,
    type_key: str,
    *,
    enabled: bool,
    key: str | None = None,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    return await svc.client.put(
        put_url(type_key),
        json=body if body is not None else {"enabled": enabled, **REASON},
        headers={**svc.types_admin(ADMIN), "Idempotency-Key": key or f"idem-{uuid7().hex}"},
    )


async def listing(svc: Service) -> dict[str, dict[str, Any]]:
    response = await svc.client.get("/admin/v1/notification-types", headers=svc.types_admin())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return {item["type"]: item for item in response.json()["items"]}


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("GET", "/admin/v1/notification-types", {}),
        ("PUT", put_url("level_up"), {"json": {"enabled": False, **REASON}}),
    ],
)
async def test_both_permissions_are_required(
    notifications: Service, method: str, url: str, kwargs: dict[str, Any]
) -> None:
    svc = notifications

    async def call(headers: dict[str, str]) -> httpx.Response:
        return await svc.client.request(
            method, url, headers={"Idempotency-Key": "idem-perm-0001", **headers}, **kwargs
        )

    assert_problem(await call({}), 401, "auth.unauthenticated")
    for perms in ((), ("admin.access",), (PERM,), ("admin.access", "notifications.broadcast.send")):
        assert_problem(await call(svc.auth(perms=perms)), 403, "auth.forbidden")
    assert (await call(svc.types_admin())).status_code == 200


async def test_list_shows_every_catalog_type_enabled_by_default(notifications: Service) -> None:
    svc = notifications
    items = await listing(svc)
    assert set(items) == {t.key for t in svc.container.catalog.types}
    for item in items.values():
        assert item["enabled"] is True
        assert item["default_enabled"] is True
        assert item["changed_by"] is None and item["changed_at"] is None


async def test_disable_is_saved_audited_and_visible(notifications: Service) -> None:
    svc = notifications
    response = await put(svc, "level_up", enabled=False)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["type"], body["enabled"], body["changed_by"]) == ("level_up", False, str(ADMIN))
    assert body["changed_at"] is not None

    assert (await listing(svc))["level_up"]["enabled"] is False
    assert (await listing(svc))["badge_unlocked"]["enabled"] is True
    [flag] = await svc.rows(notification_type_flags)
    assert (flag.type, flag.enabled, flag.changed_by) == ("level_up", False, ADMIN)
    [action] = await svc.rows(admin_actions)
    assert (action.action, action.target, action.reason) == (
        "notification_type.set_enabled",
        "level_up",
        REASON["reason"],
    )
    assert action.details == {"enabled": False, "previous": True}


async def test_disabled_type_creates_no_notification_and_enabling_restores(
    notifications: Service,
) -> None:
    svc = notifications
    user = uuid7()
    await put(svc, "level_up", enabled=False)

    envelope = svc.event("progression.ProgressionLevelChanged", user_id=str(user), new_level=3)
    assert await svc.deliver(envelope) is Outcome.PROCESSED  # событие не падает и не уходит в DLQ
    assert await svc.count(notifications_table) == 0
    assert svc.dead_letters.letters == []

    await put(svc, "level_up", enabled=True)
    again = svc.event("progression.ProgressionLevelChanged", user_id=str(user), new_level=4)
    assert await svc.deliver(again) is Outcome.PROCESSED
    [row] = await svc.rows(notifications_table)
    assert (row.user_id, row.type) == (user, "level_up")


async def test_other_types_keep_working_while_one_is_disabled(notifications: Service) -> None:
    svc = notifications
    user = str(uuid7())
    await put(svc, "level_up", enabled=False)
    assert (
        await svc.deliver(svc.event("progression.BadgeUnlocked", user_id=user))
    ) is Outcome.PROCESSED
    [row] = await svc.rows(notifications_table)
    assert row.type == "badge_unlocked"


async def test_disabled_audience_type_is_not_fanned_out(notifications: Service) -> None:
    svc = notifications
    await svc.notify(uuid7(), new_level=2)
    await put(svc, "event_started", enabled=False)
    await svc.deliver(svc.event("progression.EventRunStarted"))
    for task in list(svc.tasks.tasks):
        await build_task_handlers(svc.infra)[task["task_type"]](task["payload"])
    started = await svc.rows(notifications_table, notifications_table.c.type == "event_started")
    assert started == []


async def test_user_preferences_and_broadcasts_are_not_affected(notifications: Service) -> None:
    svc = notifications
    user: UUID = uuid7()
    saved = await svc.client.put(
        "/v1/notification-settings/level_up",
        json={"in_app": False, "email": False},
        headers=svc.auth(user),
    )
    assert saved.status_code == 200

    await put(svc, "level_up", enabled=False)
    await put(svc, "level_up", enabled=True)
    [pref] = await svc.rows(notification_preferences)
    assert (pref.user_id, pref.type, pref.in_app) == (user, "level_up", False)  # выбор игрока цел

    settings = await svc.client.get("/v1/notification-settings", headers=svc.auth(user))
    assert settings.status_code == 200

    # Рассылка - не тип уведомления: отключённые типы ей не мешают.
    for type_key in ("level_up", "badge_unlocked"):
        await put(svc, type_key, enabled=False)
    broadcast = await svc.client.post(
        "/admin/v1/broadcasts",
        json={"subject": "Новости", "body": "Текст", "reason": "анонс обновления"},
        headers={**svc.admin(), "Idempotency-Key": "idem-broadcast-0001"},
    )
    assert broadcast.status_code == 202
    assert svc.tasks.tasks[-1]["task_type"] == BROADCAST_TASK


async def test_unknown_type_is_404(notifications: Service) -> None:
    svc = notifications
    assert_problem(
        await put(svc, "no_such_type", enabled=False), 404, "notifications.type_not_found"
    )
    assert await svc.count(notification_type_flags) == 0


async def test_same_key_is_idempotent_and_other_body_is_conflict(notifications: Service) -> None:
    svc = notifications
    first = await put(svc, "level_up", enabled=False, key="idem-types-0001")
    again = await put(svc, "level_up", enabled=False, key="idem-types-0001")
    assert again.status_code == 200 and again.json() == first.json()
    assert await svc.count(admin_actions) == 1

    other = await put(svc, "level_up", enabled=True, key="idem-types-0001")
    assert_problem(other, 409, "idempotency.key_reused")
    assert (await listing(svc))["level_up"]["enabled"] is False


async def test_repeated_state_with_new_key_keeps_who_changed(notifications: Service) -> None:
    svc = notifications
    await put(svc, "level_up", enabled=False)
    second = await put(svc, "level_up", enabled=False)
    assert second.status_code == 200
    [action_a, action_b] = sorted(await svc.rows(admin_actions), key=lambda a: a.at)
    assert action_b.details == {"enabled": False, "previous": False}
    assert action_a.details["previous"] is True


async def test_validation_errors(notifications: Service) -> None:
    svc = notifications
    no_key = await svc.client.put(
        put_url("level_up"), json={"enabled": False, **REASON}, headers=svc.types_admin()
    )
    assert_problem(no_key, 422, "idempotency.key_required")
    assert_problem(
        await put(svc, "level_up", enabled=False, body={"enabled": False}),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await put(svc, "level_up", enabled=False, body={"enabled": False, "reason": "ab"}),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await put(svc, "level_up", enabled=False, body={"enabled": "maybe", **REASON}),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await put(svc, "level_up", enabled=False, body={"enabled": False, "extra": 1, **REASON}),
        422,
        "request.validation_failed",
    )
    assert await svc.count(notification_type_flags) == 0


SYSTEM_TYPES = ("avatar_approved", "avatar_rejected", "report_resolved")


async def test_list_shows_switchable_flag(notifications: Service) -> None:
    items = await listing(notifications)
    assert items["level_up"]["switchable"] is True
    for type_key in SYSTEM_TYPES:
        assert items[type_key]["switchable"] is False


@pytest.mark.parametrize("type_key", SYSTEM_TYPES)
async def test_system_type_cannot_be_switched_off(notifications: Service, type_key: str) -> None:
    svc = notifications
    assert_problem(
        await put(svc, type_key, enabled=False), 422, "notifications.type_not_switchable"
    )
    assert await svc.count(notification_type_flags) == 0
    assert await svc.count(admin_actions) == 0


async def test_old_disabled_flag_does_not_affect_system_type(notifications: Service) -> None:
    svc = notifications
    user = uuid7()
    async with svc.infra.sessions() as session, session.begin():
        await session.execute(
            notification_type_flags.insert().values(
                type="avatar_approved",
                enabled=False,
                changed_by=ADMIN,
                changed_at=datetime.now(UTC),
            )
        )
    envelope = svc.event(
        "media_pipeline.AssetModerated",
        owner_user_id=str(user),
        status="approved",
        reason_code=None,
    )
    assert await svc.deliver(envelope) is Outcome.PROCESSED
    [row] = await svc.rows(notifications_table)
    assert (row.user_id, row.type) == (user, "avatar_approved")
