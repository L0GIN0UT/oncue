"""Отписка по ссылке: единственная мутация без входа (план этапа 5, §9.8, RFC 8058)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from notifications_service.application.broadcasts import ONE_CLICK_PATH
from notifications_service.domain.tokens import hash_token
from notifications_service.infrastructure.db.tables import broadcast_deliveries, email_subscriptions
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py
INVALID = "notifications.unsubscribe_invalid"
ONE_CLICK = "/v1/unsubscribe/one-click"
FORM = {"content-type": "application/x-www-form-urlencoded"}
ONE_CLICK_BODY = "List-Unsubscribe=One-Click"
ADMIN = uuid7()


async def subscribe(svc: Service, user: UUID) -> None:
    response = await svc.client.put(
        "/v1/email-subscription", json={"subscribed": True}, headers=svc.auth(user)
    )
    assert response.status_code == 200
    svc.directory.emails[user] = f"{user.hex[-8:]}@example.test"


async def is_subscribed(svc: Service, user: UUID) -> bool:
    [row] = await svc.rows(email_subscriptions, email_subscriptions.c.user_id == user)
    return bool(row.subscribed)


async def issue(svc: Service, user: UUID) -> str:
    """Токен отписки пользователя: настоящая рассылка, токен берётся из его письма."""
    created = await svc.client.post(
        "/admin/v1/broadcasts",
        json={"subject": "Новости", "body": "Текст", "reason": "тест отписки"},
        headers={**svc.admin(ADMIN), "Idempotency-Key": f"idem-{uuid7().hex}"},
    )
    await svc.container.send_broadcast.execute(UUID(created.json()["id"]))
    [message] = [m for m in svc.email.sent if m.to == svc.directory.emails[user]][-1:]
    match = re.search(r"/unsubscribe\?token=(\S+)", message.body)
    assert match is not None
    return match.group(1)


def one_click(svc: Service, token: str, body: str = ONE_CLICK_BODY) -> Any:
    return svc.client.post(f"{ONE_CLICK}?token={token}", content=body, headers=FORM)


async def test_valid_token_unsubscribes_without_login(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    response = await svc.client.post("/v1/unsubscribe", json={"token": token})
    assert response.status_code == 204
    assert await is_subscribed(svc, user) is False


async def test_token_is_opaque_and_only_its_hash_is_stored(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    assert len(token) == 43
    assert user.hex not in token and str(user) not in token
    [row] = await svc.rows(broadcast_deliveries)
    assert row.token_hash == hash_token(token) != token
    assert row.token_expires_at == svc.clock.now() + timedelta(days=90)


async def test_repeat_of_valid_token_is_204(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    first = await svc.client.post("/v1/unsubscribe", json={"token": token})
    again = await svc.client.post("/v1/unsubscribe", json={"token": token})
    assert (first.status_code, again.status_code) == (204, 204)
    assert await is_subscribed(svc, user) is False


async def test_token_only_affects_its_owner(notifications: Service) -> None:
    svc, owner, other = notifications, uuid7(), uuid7()
    await subscribe(svc, owner)
    await subscribe(svc, other)
    token = await issue(svc, owner)
    assert (await svc.client.post("/v1/unsubscribe", json={"token": token})).status_code == 204
    assert await is_subscribed(svc, owner) is False
    assert await is_subscribed(svc, other) is True


async def test_tokens_of_one_broadcast_differ_per_recipient(notifications: Service) -> None:
    svc, a, b = notifications, uuid7(), uuid7()
    await subscribe(svc, a)
    await subscribe(svc, b)
    created = await svc.client.post(
        "/admin/v1/broadcasts",
        json={"subject": "Новости", "body": "Текст", "reason": "тест отписки"},
        headers={**svc.admin(ADMIN), "Idempotency-Key": "idem-two-recipients"},
    )
    await svc.container.send_broadcast.execute(UUID(created.json()["id"]))
    found = [re.search(r"token=(\S+)", m.body) for m in svc.email.sent]
    assert len({m.group(1) for m in found if m is not None}) == 2


async def test_expired_token_is_rejected_like_unknown(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    svc.clock.advance(days=91)
    expired = await svc.client.post("/v1/unsubscribe", json={"token": token})
    unknown = await svc.client.post("/v1/unsubscribe", json={"token": "x" * 43})
    a, b = assert_problem(expired, 422, INVALID), assert_problem(unknown, 422, INVALID)
    for body in (a, b):
        body.pop("correlation_id", None)
    assert a == b
    assert await is_subscribed(svc, user) is True


async def test_user_without_subscription_row_gets_no_row_from_invalid_token(
    notifications: Service,
) -> None:
    svc = notifications
    response = await svc.client.post("/v1/unsubscribe", json={"token": "y" * 43})
    assert_problem(response, 422, INVALID)
    assert await svc.count(email_subscriptions) == 0


@pytest.mark.parametrize(
    "token", ["x" * 43, "short", "", "é" * 40, "....................", "9" * 600]
)
async def test_garbage_tokens_give_the_same_answer_as_unknown_ones(
    notifications: Service, token: str
) -> None:
    svc = notifications
    garbage = assert_problem(
        await svc.client.post("/v1/unsubscribe", json={"token": token}), 422, INVALID
    )
    unknown = assert_problem(
        await svc.client.post("/v1/unsubscribe", json={"token": "z" * 43}), 422, INVALID
    )
    for body in (garbage, unknown):
        body.pop("correlation_id", None)
    assert garbage == unknown  # неразличимы: ни тела, ни статуса


async def test_request_shape_errors_are_validation_errors(notifications: Service) -> None:
    svc = notifications
    extra = await svc.client.post(
        "/v1/unsubscribe", json={"token": "x" * 43, "user_id": str(uuid7())}
    )
    assert_problem(extra, 422, "request.validation_failed")


async def test_attempts_are_limited_per_ip_with_retry_after(notifications: Service) -> None:
    svc = notifications  # лимит из фикстуры: 3 попытки за 60 с
    statuses = []
    for _ in range(4):
        response = await svc.client.post("/v1/unsubscribe", json={"token": "x" * 43})
        statuses.append(response.status_code)
    assert statuses == [422, 422, 422, 429]
    assert_problem(response, 429, "rate.limited")
    assert int(response.headers["retry-after"]) >= 1


async def test_garbage_of_any_shape_is_counted_by_the_limit(notifications: Service) -> None:
    """Лимит стоит до разбора токена: слишком короткий/длинный мусор тоже расходует попытки."""
    svc = notifications
    for token in ("", "a", "b" * 5000):
        await svc.client.post("/v1/unsubscribe", json={"token": token})
    response = await svc.client.post("/v1/unsubscribe", json={"token": "x" * 43})
    assert response.status_code == 429


async def test_limit_also_blocks_valid_token_once_exhausted(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    for _ in range(3):
        await svc.client.post("/v1/unsubscribe", json={"token": "x" * 43})
    response = await svc.client.post("/v1/unsubscribe", json={"token": token})
    assert response.status_code == 429
    assert await is_subscribed(svc, user) is True


async def test_unavailable_redis_fails_closed_with_retry_after(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    original = svc.infra.redis
    svc.infra.redis = broken
    try:
        response = await svc.client.post("/v1/unsubscribe", json={"token": token})
    finally:
        svc.infra.redis = original
        await broken.aclose()
    assert_problem(response, 503, "dependency.unavailable")
    assert int(response.headers["retry-after"]) >= 1
    assert await is_subscribed(svc, user) is True


# ------------------------------------------------------------------ one-click (RFC 8058)
async def test_one_click_form_post_unsubscribes(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    response = await one_click(svc, token)
    assert response.status_code == 204
    assert await is_subscribed(svc, user) is False
    assert (await one_click(svc, token)).status_code == 204  # повтор


async def test_list_unsubscribe_header_url_accepts_the_mail_clients_post(
    notifications: Service,
) -> None:
    """Ровно то, что делает почтовик: POST на URL из заголовка с телом формы."""
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    header = svc.email.sent[-1].headers["List-Unsubscribe"]
    url = header.strip("<>")
    assert url.startswith(svc.container.public_base_url + ONE_CLICK_PATH)
    path = url.removeprefix(svc.container.public_base_url).removeprefix("/api/notifications")
    response = await svc.client.post(path, content=ONE_CLICK_BODY, headers=FORM)
    assert response.status_code == 204
    assert token in url
    assert await is_subscribed(svc, user) is False


async def test_one_click_rejects_get(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    response = await svc.client.get(f"{ONE_CLICK}?token={token}")
    assert response.status_code == 405
    assert await is_subscribed(svc, user) is True


@pytest.mark.parametrize(
    "body",
    [
        "List-Unsubscribe=No",
        "",
        "other=1",
        "List-Unsubscribe=One-Click&List-Unsubscribe=No",
        "a=" + "b" * 5000,
        "List-Unsubscribe=One-Click&pad=" + "b" * 2000,
    ],
)
async def test_one_click_bad_body_is_the_same_as_bad_token(
    notifications: Service, body: str
) -> None:
    """Верный токен с неверным телом не отписывает и неотличим от неизвестного токена."""
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    unknown = assert_problem(await one_click(svc, "q" * 43), 422, INVALID)
    wrong = assert_problem(await one_click(svc, token, body), 422, INVALID)
    for problem in (unknown, wrong):
        problem.pop("correlation_id", None)
    assert wrong == unknown
    assert await is_subscribed(svc, user) is True


async def test_one_click_without_token_is_invalid(notifications: Service) -> None:
    svc = notifications
    response = await svc.client.post(ONE_CLICK, content=ONE_CLICK_BODY, headers=FORM)
    assert_problem(response, 422, INVALID)


async def test_one_click_expired_token_is_rejected(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    svc.clock.advance(days=91)
    assert_problem(await one_click(svc, token), 422, INVALID)
    assert await is_subscribed(svc, user) is True


async def test_one_click_shares_the_ip_limit(notifications: Service) -> None:
    svc = notifications
    statuses = [(await one_click(svc, "x" * 43)).status_code for _ in range(4)]
    assert statuses == [422, 422, 422, 429]
    # общий счётчик с обычной ручкой
    assert (await svc.client.post("/v1/unsubscribe", json={"token": "x" * 43})).status_code == 429


# ---------------------------------------- форма на основной ручке (RFC 8058)
async def test_one_click_accepts_content_type_with_charset(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    headers = {"content-type": "application/x-www-form-urlencoded; charset=UTF-8"}
    response = await svc.client.post(
        f"{ONE_CLICK}?token={token}", content=ONE_CLICK_BODY, headers=headers
    )
    assert response.status_code == 204
    assert await is_subscribed(svc, user) is False


async def test_main_endpoint_accepts_one_click_form_with_token_in_query(
    notifications: Service,
) -> None:
    """Почтовик может прислать форму и на `/v1/unsubscribe`: токен в query, как в ссылке."""
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    response = await svc.client.post(
        f"/v1/unsubscribe?token={token}", content=ONE_CLICK_BODY, headers=FORM
    )
    assert response.status_code == 204
    assert await is_subscribed(svc, user) is False


async def test_main_endpoint_json_still_works(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    response = await svc.client.post("/v1/unsubscribe", json={"token": token})
    assert response.status_code == 204
    assert await is_subscribed(svc, user) is False


@pytest.mark.parametrize("body", ["List-Unsubscribe=No", "", "other=1"])
async def test_main_endpoint_form_with_bad_body_is_invalid(
    notifications: Service, body: str
) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = await issue(svc, user)
    response = await svc.client.post(f"/v1/unsubscribe?token={token}", content=body, headers=FORM)
    assert_problem(response, 422, INVALID)
    assert await is_subscribed(svc, user) is True
