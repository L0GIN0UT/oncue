"""Отписка по ссылке: единственная мутация без входа (план этапа 5, §9.8)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from notifications_service.domain.tokens import UnsubscribeTokens
from notifications_service.infrastructure.db.tables import email_subscriptions
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py
INVALID = "notifications.unsubscribe_invalid"


async def subscribe(svc: Service, user: UUID) -> None:
    response = await svc.client.put(
        "/v1/email-subscription", json={"subscribed": True}, headers=svc.auth(user)
    )
    assert response.status_code == 200


async def is_subscribed(svc: Service, user: UUID) -> bool:
    [row] = await svc.rows(email_subscriptions, email_subscriptions.c.user_id == user)
    return bool(row.subscribed)


def token_for(svc: Service, user: UUID, broadcast: UUID | None = None) -> str:
    return str(svc.container.tokens.issue(user, broadcast or uuid7()))


async def test_valid_token_unsubscribes_without_login(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    response = await svc.client.post("/v1/unsubscribe", json={"token": token_for(svc, user)})
    assert response.status_code == 204
    assert await is_subscribed(svc, user) is False


async def test_repeat_of_valid_token_is_204(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    token = token_for(svc, user)
    first = await svc.client.post("/v1/unsubscribe", json={"token": token})
    again = await svc.client.post("/v1/unsubscribe", json={"token": token})
    assert (first.status_code, again.status_code) == (204, 204)
    assert await is_subscribed(svc, user) is False


async def test_user_without_subscription_row_gets_204_and_no_row(notifications: Service) -> None:
    svc = notifications
    response = await svc.client.post("/v1/unsubscribe", json={"token": token_for(svc, uuid7())})
    assert response.status_code == 204
    assert await svc.count(email_subscriptions) == 0


async def test_foreign_token_does_not_touch_another_user(notifications: Service) -> None:
    """Токен, подписанный другим ключом, и подмена user_id не отписывают игрока."""
    svc, victim = notifications, uuid7()
    await subscribe(svc, victim)
    other_key = UnsubscribeTokens(active_version=1, keys={1: b"z" * 32})
    foreign = other_key.issue(victim, uuid7())
    response = await svc.client.post("/v1/unsubscribe", json={"token": foreign})
    assert_problem(response, 422, INVALID)

    version, _ids, mac = token_for(svc, uuid7()).split(".")
    forged_ids = UnsubscribeTokens(active_version=1, keys={1: b"z" * 32}).issue(victim, uuid7())
    forged = f"{version}.{forged_ids.split('.')[1]}.{mac}"
    assert_problem(await svc.client.post("/v1/unsubscribe", json={"token": forged}), 422, INVALID)
    assert await is_subscribed(svc, victim) is True


@pytest.mark.parametrize(
    "token",
    ["x" * 32, "1." + "A" * 43 + "." + "B" * 43, "....................", "é" * 40, "9" * 40],
)
async def test_garbage_tokens_give_the_same_answer_as_forged_ones(
    notifications: Service, token: str
) -> None:
    svc = notifications
    garbage = await svc.client.post("/v1/unsubscribe", json={"token": token})
    forged = await svc.client.post(
        "/v1/unsubscribe",
        json={
            "token": UnsubscribeTokens(active_version=1, keys={1: b"z" * 32}).issue(
                uuid7(), uuid7()
            )
        },
    )
    body_g = assert_problem(garbage, 422, INVALID)
    body_f = assert_problem(forged, 422, INVALID)
    for body in (body_g, body_f):
        body.pop("correlation_id", None)
    assert body_g == body_f  # неразличимы: ни тела, ни статуса


async def test_request_shape_errors_are_validation_errors(notifications: Service) -> None:
    svc = notifications
    short = await svc.client.post("/v1/unsubscribe", json={"token": "short"})
    assert_problem(short, 422, "request.validation_failed")
    extra = await svc.client.post(
        "/v1/unsubscribe", json={"token": token_for(svc, uuid7()), "user_id": str(uuid7())}
    )
    assert_problem(extra, 422, "request.validation_failed")


async def test_attempts_are_limited_per_ip_with_retry_after(notifications: Service) -> None:
    svc = notifications  # лимит из фикстуры: 3 попытки за 60 с
    statuses = []
    for _ in range(4):
        response = await svc.client.post("/v1/unsubscribe", json={"token": "x" * 32})
        statuses.append(response.status_code)
    assert statuses == [422, 422, 422, 429]
    assert_problem(response, 429, "rate.limited")
    assert int(response.headers["retry-after"]) >= 1


async def test_limit_also_blocks_valid_token_once_exhausted(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    for _ in range(3):
        await svc.client.post("/v1/unsubscribe", json={"token": "x" * 32})
    response = await svc.client.post("/v1/unsubscribe", json={"token": token_for(svc, user)})
    assert response.status_code == 429
    assert await is_subscribed(svc, user) is True


async def test_unavailable_redis_fails_closed_with_retry_after(notifications: Service) -> None:
    svc, user = notifications, uuid7()
    await subscribe(svc, user)
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    original = svc.infra.redis
    svc.infra.redis = broken
    try:
        response = await svc.client.post("/v1/unsubscribe", json={"token": token_for(svc, user)})
    finally:
        svc.infra.redis = original
        await broken.aclose()
    assert_problem(response, 503, "dependency.unavailable")
    assert int(response.headers["retry-after"]) >= 1
    assert await is_subscribed(svc, user) is True
