"""Рассылки администрации: постановка (admin API) и отправка (воркер)."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import httpx
import pytest
from notifications_service.application.broadcasts import (
    BROADCAST_TASK,
    BroadcastNotVisibleError,
)
from notifications_service.infrastructure.db.tables import (
    admin_actions,
    broadcast_deliveries,
    broadcasts,
)
from notifications_service.infrastructure.email import UnconfiguredEmailDirectory

from gv_core.ids import uuid7
from gv_messaging.ports import PermanentError
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py
KEY = {"Idempotency-Key": "idem-broadcast-0001"}
BODY = {"subject": "Новости", "body": "Текст рассылки", "reason": "анонс обновления"}
PERM = "notifications.broadcast.send"
ADMIN = uuid7()  # один админ: ключи идемпотентности действуют в рамках актора


async def create(
    svc: Service, body: dict[str, Any] | None = None, key: str = KEY["Idempotency-Key"]
) -> httpx.Response:
    return await svc.client.post(
        "/admin/v1/broadcasts",
        json=body or BODY,
        headers={**svc.admin(ADMIN), "Idempotency-Key": key},
    )


async def subscriber(svc: Service, email: str | None = None) -> UUID:
    user = uuid7()
    await svc.client.put(
        "/v1/email-subscription", json={"subscribed": True}, headers=svc.auth(user)
    )
    svc.directory.emails[user] = email or f"{user.hex[-8:]}@example.test"
    return user


async def run_worker(svc: Service, broadcast_id: str) -> None:
    await svc.container.send_broadcast.execute(UUID(broadcast_id))


@pytest.mark.parametrize(("method", "kwargs"), [("GET", {}), ("POST", {"json": BODY})])
async def test_specific_permission_is_required(
    notifications: Service, method: str, kwargs: dict[str, Any]
) -> None:
    svc = notifications

    async def call(headers: dict[str, str]) -> httpx.Response:
        return await svc.client.request(
            method, "/admin/v1/broadcasts", headers={**KEY, **headers}, **kwargs
        )

    assert_problem(await call({}), 401, "auth.unauthenticated")
    for perms in ((), ("admin.access",), (PERM,)):
        assert_problem(await call(svc.auth(perms=perms)), 403, "auth.forbidden")
    assert (await call(svc.admin())).status_code in {200, 202}


async def test_create_returns_snapshot_and_queues_task_and_audit(notifications: Service) -> None:
    svc = notifications
    await subscriber(svc)
    await subscriber(svc)
    response = await create(svc)
    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["status"], body["recipients"], body["subject"]) == ("queued", 2, "Новости")
    assert svc.tasks.tasks == [
        {
            "task_type": BROADCAST_TASK,
            "payload": {"broadcast_id": body["id"]},
            "task_id": body["id"],
        }
    ]
    [action] = await svc.rows(admin_actions)
    assert action.action == "broadcast.create"
    assert action.target == body["id"]
    assert action.reason == BODY["reason"]


async def test_same_key_returns_same_broadcast_without_second_task(notifications: Service) -> None:
    svc = notifications
    first = await create(svc)
    again = await create(svc)
    assert again.status_code == 202
    assert again.json() == first.json()
    assert len(svc.tasks.tasks) == 1
    assert await svc.count(broadcasts) == 1


async def test_same_key_with_other_body_is_conflict(notifications: Service) -> None:
    svc = notifications
    await create(svc)
    other = await create(svc, {**BODY, "subject": "Другая тема"})
    assert_problem(other, 409, "idempotency.key_reused")
    assert await svc.count(broadcasts) == 1


async def test_validation_errors(notifications: Service) -> None:
    svc = notifications
    no_key = await svc.client.post("/admin/v1/broadcasts", json=BODY, headers=svc.admin())
    assert_problem(no_key, 422, "idempotency.key_required")
    no_reason = await create(svc, {"subject": "Тема", "body": "Текст"})
    assert_problem(no_reason, 422, "request.validation_failed")
    extra = await create(svc, {**BODY, "audience": "all"})
    assert_problem(extra, 422, "request.validation_failed")
    assert await svc.count(broadcasts) == 0
    assert svc.tasks.tasks == []


async def test_failed_enqueue_leaves_no_broadcast_and_key_is_reusable(
    notifications: Service,
) -> None:
    svc = notifications
    original = svc.tasks.enqueue

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("rabbit down")

    svc.tasks.enqueue = broken
    failed = await create(svc)
    assert failed.status_code == 500
    assert await svc.count(broadcasts) == 0
    svc.tasks.enqueue = original
    retry = await create(svc)
    assert retry.status_code == 202
    assert await svc.count(broadcasts) == 1


async def test_list_is_paginated_newest_first(notifications: Service) -> None:
    svc = notifications
    ids = []
    for index in range(3):
        svc.clock.advance(seconds=1)
        ids.append((await create(svc, key=f"idem-list-{index:04d}")).json()["id"])
    headers = svc.admin()
    page = await svc.client.get("/admin/v1/broadcasts?limit=2", headers=headers)
    assert page.headers["cache-control"] == "no-store"
    assert [i["id"] for i in page.json()["items"]] == [ids[2], ids[1]]
    rest = await svc.client.get(
        f"/admin/v1/broadcasts?limit=2&cursor={page.json()['next_cursor']}", headers=headers
    )
    assert [i["id"] for i in rest.json()["items"]] == [ids[0]]
    assert rest.json()["next_cursor"] is None


async def test_worker_sends_only_to_subscribers_in_batches(notifications: Service) -> None:
    svc = notifications
    subscribed = [await subscriber(svc) for _ in range(5)]
    stranger = uuid7()  # согласия нет, адрес в справочнике есть
    svc.directory.emails[stranger] = "stranger@example.test"
    withdrawn = await subscriber(svc)
    await svc.client.put(
        "/v1/email-subscription", json={"subscribed": False}, headers=svc.auth(withdrawn)
    )

    broadcast = (await create(svc)).json()
    await run_worker(svc, broadcast["id"])

    sent_to = {m.to for m in svc.email.sent}
    assert sent_to == {svc.directory.emails[u] for u in subscribed}
    assert "stranger@example.test" not in sent_to
    assert svc.directory.emails[withdrawn] not in sent_to
    [row] = await svc.rows(broadcasts)
    assert (row.status, row.delivered) == ("sent", 5)
    assert await svc.count(broadcast_deliveries) == 5


async def test_worker_rerun_does_not_send_twice(notifications: Service) -> None:
    svc = notifications
    for _ in range(3):
        await subscriber(svc)
    broadcast = (await create(svc)).json()
    await run_worker(svc, broadcast["id"])
    assert len(svc.email.sent) == 3
    # повтор задачи после завершения и повтор «посреди» рассылки (статус вернули назад)
    await run_worker(svc, broadcast["id"])
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session, session.begin():
        await session.execute(broadcasts.update().values(status="queued"))
    await run_worker(svc, broadcast["id"])
    assert len(svc.email.sent) == 3
    assert await svc.count(broadcast_deliveries) == 3


async def test_partial_failure_is_retried_and_delivers_only_missing(notifications: Service) -> None:
    svc = notifications
    good = await subscriber(svc)
    bad = await subscriber(svc, "bad@example.test")
    svc.email.fail_for = {"bad@example.test"}
    broadcast = (await create(svc)).json()

    with pytest.raises(RuntimeError):
        await run_worker(svc, broadcast["id"])
    assert [m.to for m in svc.email.sent] == [svc.directory.emails[good]]
    assert await svc.count(broadcast_deliveries) == 1  # занятие неудачной отправки снято
    [row] = await svc.rows(broadcasts)
    assert row.status == "sending"

    svc.email.fail_for = set()
    await run_worker(svc, broadcast["id"])
    assert sorted(m.to for m in svc.email.sent) == sorted(
        [svc.directory.emails[good], svc.directory.emails[bad]]
    )
    [row] = await svc.rows(broadcasts)
    assert (row.status, row.delivered) == ("sent", 2)


async def test_email_has_unsubscribe_link_with_working_token(notifications: Service) -> None:
    svc = notifications
    user = await subscriber(svc)
    broadcast = (await create(svc)).json()
    await run_worker(svc, broadcast["id"])

    [message] = svc.email.sent
    assert message.subject == "Новости"
    assert message.body.startswith("Текст рассылки")
    match = re.search(r"/unsubscribe\?token=(\S+)", message.body)
    assert match is not None
    token = match.group(1)
    assert svc.container.tokens.verify(token) == (user, UUID(broadcast["id"]))

    response = await svc.client.post("/v1/unsubscribe", json={"token": token})
    assert response.status_code == 204
    state = await svc.client.get("/v1/email-subscription", headers=svc.auth(user))
    assert state.json()["subscribed"] is False


async def test_user_unsubscribed_before_send_gets_nothing(notifications: Service) -> None:
    svc = notifications
    stays = await subscriber(svc)
    leaves = await subscriber(svc)
    broadcast = (await create(svc)).json()
    await svc.client.put(
        "/v1/email-subscription", json={"subscribed": False}, headers=svc.auth(leaves)
    )
    await run_worker(svc, broadcast["id"])
    assert [m.to for m in svc.email.sent] == [svc.directory.emails[stays]]


async def test_worker_waits_for_invisible_broadcast(notifications: Service) -> None:
    with pytest.raises(BroadcastNotVisibleError):
        await run_worker(notifications, str(uuid7()))


async def test_unconfigured_directory_fails_broadcast_permanently(notifications: Service) -> None:
    svc = notifications
    await subscriber(svc)
    broadcast = (await create(svc)).json()
    sender = svc.container.send_broadcast
    sender._directory = UnconfiguredEmailDirectory()
    with pytest.raises(PermanentError):
        await sender.execute(UUID(broadcast["id"]))
    [row] = await svc.rows(broadcasts)
    assert row.status == "failed"
    assert svc.email.sent == []


@pytest.mark.parametrize("subject", ["тема\r\nBcc: evil@example.test", "тема\x00", "тема\tтема"])
async def test_subject_with_control_characters_is_rejected(
    notifications: Service, subject: str
) -> None:
    """Управляющие символы в теме (инъекция заголовков письма) - 422, рассылки нет."""
    svc = notifications
    response = await create(svc, {**BODY, "subject": subject})
    assert_problem(response, 422, "request.validation_failed")
    assert await svc.count(broadcasts) == 0
    assert svc.tasks.tasks == []


async def test_every_email_has_one_click_list_unsubscribe_headers(notifications: Service) -> None:
    svc = notifications
    user = await subscriber(svc)
    broadcast = (await create(svc)).json()
    await run_worker(svc, broadcast["id"])

    [message] = svc.email.sent
    match = re.search(r"/unsubscribe\?token=(\S+)", message.body)
    assert match is not None
    assert message.headers["List-Unsubscribe"] == (
        f"<{svc.container.public_base_url}/unsubscribe?token={match.group(1)}>"
    )
    assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert svc.container.tokens.verify(match.group(1)) == (user, UUID(broadcast["id"]))
    assert "\n" not in "".join(message.headers.values())


async def test_permanent_refusal_is_final_and_broadcast_completes(notifications: Service) -> None:
    svc = notifications
    good = await subscriber(svc)
    await subscriber(svc, "refused@example.test")
    svc.email.refuse_for = {"refused@example.test"}
    broadcast = (await create(svc)).json()

    await run_worker(svc, broadcast["id"])  # не бросает: отказ адреса не повод повторять

    [row] = await svc.rows(broadcasts)
    assert (row.status, row.delivered, row.failed) == ("sent", 1, 1)
    statuses = {d.user_id: d.status for d in await svc.rows(broadcast_deliveries)}
    assert sorted(statuses.values()) == ["failed", "sent"]
    assert statuses[good] == "sent"

    # повтор задачи (даже при сброшенном статусе) не обходит получателей заново
    async with svc.infra.sessions() as session, session.begin():
        await session.execute(broadcasts.update().values(status="queued"))
    await run_worker(svc, broadcast["id"])
    assert sorted(svc.email.attempts) == sorted(
        [svc.directory.emails[good], "refused@example.test"]
    )


async def test_broadcast_fails_when_nothing_was_delivered(notifications: Service) -> None:
    svc = notifications
    await subscriber(svc, "a@example.test")
    await subscriber(svc, "b@example.test")
    svc.email.refuse_for = {"a@example.test", "b@example.test"}
    broadcast = (await create(svc)).json()
    await run_worker(svc, broadcast["id"])
    [row] = await svc.rows(broadcasts)
    assert (row.status, row.delivered, row.failed) == ("failed", 0, 2)


async def test_retry_after_transient_failure_skips_already_handled_users(
    notifications: Service,
) -> None:
    svc = notifications
    good = await subscriber(svc)
    await subscriber(svc, "flaky@example.test")
    await subscriber(svc, "refused@example.test")
    svc.email.fail_for = {"flaky@example.test"}
    svc.email.refuse_for = {"refused@example.test"}
    broadcast = (await create(svc)).json()
    with pytest.raises(RuntimeError):
        await run_worker(svc, broadcast["id"])

    svc.email.attempts.clear()
    svc.email.fail_for = set()
    await run_worker(svc, broadcast["id"])
    assert svc.email.attempts == ["flaky@example.test"]  # good и refused не трогали
    assert svc.directory.emails[good] in {m.to for m in svc.email.sent}
    [row] = await svc.rows(broadcasts)
    assert (row.status, row.delivered, row.failed) == ("sent", 2, 1)
