"""Публичные ручки игрока: доступ, владение, идемпотентность, заголовки кэша."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest
from notifications_service.infrastructure.db.tables import devices, email_subscriptions

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
ID = "0192a3b4-c5d6-7e8f-9a0b-0000000c0001"
PRIVATE = "private, no-store"

PUBLIC: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/notifications", {}),
    ("GET", "/v1/notifications/unread-count", {}),
    ("POST", "/v1/notifications/read-all", {}),
    ("POST", f"/v1/notifications/{ID}/read", {}),
    ("GET", "/v1/notification-settings", {}),
    ("PUT", "/v1/notification-settings/level_up", {"json": {"in_app": True, "email": False}}),
    ("GET", "/v1/email-subscription", {}),
    ("PUT", "/v1/email-subscription", {"json": {"subscribed": True}}),
    ("POST", "/v1/devices", {"json": {"platform": "web", "token": "x" * 16}}),
    ("DELETE", f"/v1/devices/{ID}", {}),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), PUBLIC)
async def test_account_is_required(
    notifications: Service, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    svc = notifications

    async def call(headers: dict[str, str]) -> httpx.Response:
        return await svc.client.request(method, path, headers={**KEY, **headers}, **kwargs)

    assert_problem(await call({}), 401, "auth.unauthenticated")
    guest = {"Authorization": f"Bearer {svc.issuer.issue_guest()}"}
    assert_problem(await call(guest), 403, "auth.guest_forbidden")


async def test_list_returns_newest_first_with_cursor_and_unread(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    for level in (1, 2, 3):
        await svc.notify(user, new_level=level)
        svc.clock.advance(seconds=1)
    headers = svc.auth(user)

    first = await svc.client.get("/v1/notifications?limit=2", headers=headers)
    assert first.status_code == 200
    assert first.headers["cache-control"] == PRIVATE
    body = first.json()
    assert [i["body"][-3:-1] for i in body["items"]] == [" 3", " 2"]
    assert body["unread"] == 3
    assert body["next_cursor"] is not None

    second = await svc.client.get(
        f"/v1/notifications?limit=2&cursor={body['next_cursor']}", headers=headers
    )
    assert [i["body"][-3:-1] for i in second.json()["items"]] == [" 1"]
    assert second.json()["next_cursor"] is None


async def test_list_is_scoped_to_owner(notifications: Service) -> None:
    svc, mine, other = notifications, uuid7(), uuid7()
    await svc.notify(mine, new_level=2)
    await svc.notify(other, new_level=2)
    body = (await svc.client.get("/v1/notifications", headers=svc.auth(mine))).json()
    assert len(body["items"]) == 1
    count = await svc.client.get("/v1/notifications/unread-count", headers=svc.auth(other))
    assert count.json() == {"count": 1}
    assert count.headers["cache-control"] == PRIVATE


async def test_unread_only_filter(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await svc.notify(user, new_level=2)
    svc.clock.advance(seconds=1)
    await svc.notify(user, new_level=3)
    headers = svc.auth(user)
    items = (await svc.client.get("/v1/notifications", headers=headers)).json()["items"]
    await svc.client.post(f"/v1/notifications/{items[0]['id']}/read", headers=headers)
    unread = (await svc.client.get("/v1/notifications?unread_only=true", headers=headers)).json()
    assert [i["id"] for i in unread["items"]] == [items[1]["id"]]
    assert unread["unread"] == 1


async def test_broken_cursor_is_422(notifications: Service) -> None:
    response = await notifications.client.get(
        "/v1/notifications?cursor=abc", headers=notifications.auth()
    )
    assert_problem(response, 422, "cursor.invalid")


async def test_mark_read_is_idempotent_and_keeps_first_time(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await svc.notify(user, new_level=2)
    headers = svc.auth(user)
    note_id = (await svc.client.get("/v1/notifications", headers=headers)).json()["items"][0]["id"]

    first = await svc.client.post(f"/v1/notifications/{note_id}/read", headers=headers)
    assert first.status_code == 204
    assert first.headers["cache-control"] == PRIVATE
    read_at = (await svc.client.get("/v1/notifications", headers=headers)).json()["items"][0]
    svc.clock.advance(seconds=30)
    again = await svc.client.post(f"/v1/notifications/{note_id}/read", headers=headers)
    assert again.status_code == 204
    after = (await svc.client.get("/v1/notifications", headers=headers)).json()["items"][0]
    assert after["read_at"] == read_at["read_at"] is not None
    assert (await svc.client.get("/v1/notifications/unread-count", headers=headers)).json() == {
        "count": 0
    }


async def test_foreign_or_missing_notification_is_404_and_stays_unread(
    notifications: Service,
) -> None:
    svc, owner, intruder = notifications, uuid7(), uuid7()
    await svc.notify(owner, new_level=2)
    note_id = (await svc.client.get("/v1/notifications", headers=svc.auth(owner))).json()["items"][
        0
    ]["id"]
    foreign = await svc.client.post(f"/v1/notifications/{note_id}/read", headers=svc.auth(intruder))
    assert_problem(foreign, 404, "notifications.not_found")
    missing = await svc.client.post(f"/v1/notifications/{ID}/read", headers=svc.auth(owner))
    assert_problem(missing, 404, "notifications.not_found")
    count = await svc.client.get("/v1/notifications/unread-count", headers=svc.auth(owner))
    assert count.json() == {"count": 1}


async def test_read_all_marks_only_own_and_is_idempotent(notifications: Service) -> None:
    svc, mine, other = notifications, uuid7(), uuid7()
    await svc.notify(mine, new_level=2)
    await svc.notify(mine, new_level=3)
    await svc.notify(other, new_level=2)
    first = await svc.client.post("/v1/notifications/read-all", headers=svc.auth(mine))
    assert first.json() == {"marked": 2}
    assert first.headers["cache-control"] == PRIVATE
    again = await svc.client.post("/v1/notifications/read-all", headers=svc.auth(mine))
    assert again.json() == {"marked": 0}
    count = await svc.client.get("/v1/notifications/unread-count", headers=svc.auth(other))
    assert count.json() == {"count": 1}


async def test_settings_list_has_all_types_from_config(notifications: Service) -> None:
    response = await notifications.client.get(
        "/v1/notification-settings", headers=notifications.auth()
    )
    assert response.headers["cache-control"] == PRIVATE
    types = {t["type"]: t for t in response.json()["types"]}
    assert set(types) >= {"level_up", "avatar_approved", "avatar_rejected"}
    assert types["level_up"] == {
        "type": "level_up",
        "title": "Новый уровень",
        "in_app": True,
        "email": False,
        "user_mutable": True,
    }
    assert types["avatar_approved"]["user_mutable"] is False


async def test_setting_change_is_saved_and_idempotent(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    body = {"in_app": False, "email": True}
    for _ in range(2):
        response = await svc.client.put(
            "/v1/notification-settings/badge_unlocked", json=body, headers=svc.auth(user)
        )
        assert response.status_code == 200
        assert response.json()["in_app"] is False
        assert response.headers["cache-control"] == PRIVATE
    listed = (await svc.client.get("/v1/notification-settings", headers=svc.auth(user))).json()
    badge = next(t for t in listed["types"] if t["type"] == "badge_unlocked")
    assert (badge["in_app"], badge["email"]) == (False, True)
    other = (await svc.client.get("/v1/notification-settings", headers=svc.auth())).json()
    assert next(t for t in other["types"] if t["type"] == "badge_unlocked")["in_app"] is True


async def test_setting_unknown_type_is_404_and_non_mutable_is_422(notifications: Service) -> None:
    svc = notifications
    body = {"in_app": False, "email": False}
    unknown = await svc.client.put("/v1/notification-settings/nope", json=body, headers=svc.auth())
    assert_problem(unknown, 404, "notifications.type_not_found")
    fixed = await svc.client.put(
        "/v1/notification-settings/avatar_rejected", json=body, headers=svc.auth()
    )
    assert_problem(fixed, 422, "notifications.type_not_mutable")


async def test_setting_rejects_extra_field_and_missing_field(notifications: Service) -> None:
    svc = notifications
    extra = await svc.client.put(
        "/v1/notification-settings/level_up",
        json={"in_app": True, "email": False, "color": "red"},
        headers=svc.auth(),
    )
    assert_problem(extra, 422, "request.validation_failed")
    missing = await svc.client.put(
        "/v1/notification-settings/level_up", json={"in_app": True}, headers=svc.auth()
    )
    assert_problem(missing, 422, "request.validation_failed")


async def test_subscription_defaults_to_none_and_toggles(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    default = await svc.client.get("/v1/email-subscription", headers=svc.auth(user))
    assert default.json() == {"subscribed": False, "changed_at": None}
    assert default.headers["cache-control"] == PRIVATE

    on = await svc.client.put(
        "/v1/email-subscription", json={"subscribed": True}, headers=svc.auth(user)
    )
    assert on.json()["subscribed"] is True
    changed_at = on.json()["changed_at"]
    svc.clock.advance(seconds=60)
    again = await svc.client.put(
        "/v1/email-subscription", json={"subscribed": True}, headers=svc.auth(user)
    )
    assert again.json()["changed_at"] == changed_at  # повтор не двигает время
    off = await svc.client.put(
        "/v1/email-subscription", json={"subscribed": False}, headers=svc.auth(user)
    )
    assert off.json()["subscribed"] is False
    assert await svc.count(email_subscriptions) == 1


async def test_subscription_rejects_extra_field(notifications: Service) -> None:
    response = await notifications.client.put(
        "/v1/email-subscription",
        json={"subscribed": True, "email": "a@b.c"},
        headers=notifications.auth(),
    )
    assert_problem(response, 422, "request.validation_failed")


async def test_device_registration_is_idempotent_by_token_and_hashes_it(
    notifications: Service,
) -> None:
    svc, user = notifications, uuid7()
    body = {"platform": "ios", "token": "secret-device-token-123"}
    first = await svc.client.post("/v1/devices", json=body, headers={**svc.auth(user), **KEY})
    second = await svc.client.post("/v1/devices", json=body, headers={**svc.auth(user), **KEY})
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] == second.json()["id"]
    assert first.headers["cache-control"] == PRIVATE
    [row] = await svc.rows(devices)
    assert row.token_hash != body["token"]
    assert len(row.token_hash) == 64
    assert "secret-device-token" not in repr(row)


async def test_device_requires_idempotency_key_and_valid_body(notifications: Service) -> None:
    svc = notifications
    body = {"platform": "web", "token": "x" * 16}
    no_key = await svc.client.post("/v1/devices", json=body, headers=svc.auth())
    assert_problem(no_key, 422, "idempotency.key_required")
    extra = await svc.client.post(
        "/v1/devices", json={**body, "extra": 1}, headers={**svc.auth(), **KEY}
    )
    assert_problem(extra, 422, "request.validation_failed")
    bad_platform = await svc.client.post(
        "/v1/devices", json={**body, "platform": "tv"}, headers={**svc.auth(), **KEY}
    )
    assert_problem(bad_platform, 422, "request.validation_failed")


async def test_device_delete_own_and_foreign(notifications: Service) -> None:
    svc, owner, intruder = notifications, uuid7(), uuid7()
    created = await svc.client.post(
        "/v1/devices",
        json={"platform": "android", "token": "t" * 20},
        headers={**svc.auth(owner), **KEY},
    )
    device_id = UUID(created.json()["id"])
    foreign = await svc.client.delete(f"/v1/devices/{device_id}", headers=svc.auth(intruder))
    assert_problem(foreign, 404, "notifications.device_not_found")
    assert await svc.count(devices, devices.c.deleted_at.is_(None)) == 1
    own = await svc.client.delete(f"/v1/devices/{device_id}", headers=svc.auth(owner))
    assert own.status_code == 204
    assert await svc.count(devices, devices.c.deleted_at.is_(None)) == 0

    # повтор удаления своего уже удалённого устройства - успех (идемпотентно)
    again = await svc.client.delete(f"/v1/devices/{device_id}", headers=svc.auth(owner))
    assert again.status_code == 204
    # чужое (даже удалённое) и никогда не существовавшее - 404
    foreign_after = await svc.client.delete(f"/v1/devices/{device_id}", headers=svc.auth(intruder))
    assert_problem(foreign_after, 404, "notifications.device_not_found")
    never = await svc.client.delete(f"/v1/devices/{uuid7()}", headers=svc.auth(owner))
    assert_problem(never, 404, "notifications.device_not_found")


async def test_registering_deleted_token_revives_same_device(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    body = {"platform": "web", "token": "r" * 20}
    first = (
        await svc.client.post("/v1/devices", json=body, headers={**svc.auth(user), **KEY})
    ).json()
    await svc.client.delete(f"/v1/devices/{first['id']}", headers=svc.auth(user))
    again = (
        await svc.client.post("/v1/devices", json=body, headers={**svc.auth(user), **KEY})
    ).json()
    assert again["id"] == first["id"]
    assert await svc.count(devices, devices.c.deleted_at.is_(None)) == 1
