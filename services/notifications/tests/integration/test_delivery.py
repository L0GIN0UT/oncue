"""Доставка: Centrifugo (фейк-сервер через respx), параллельная дедупликация, лимитер в Redis."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx
from notifications_service.infrastructure.db.tables import notifications as notifications_table
from notifications_service.infrastructure.limiter import RedisAttemptLimiter
from notifications_service.infrastructure.realtime import (
    CentrifugoPublisher,
    PostCommitDispatcher,
)
from pydantic import SecretStr

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_service import RealtimeSettings

pytestmark = pytest.mark.integration

Service = Any  # фикстура `notifications` описана в conftest.py
API = "http://centrifugo.test/api"
SETTINGS = RealtimeSettings(api_url=API, api_key=SecretStr("a" * 40), timeout_s=1.0)


def real_publisher() -> CentrifugoPublisher:
    return CentrifugoPublisher(SETTINGS)


async def use_centrifugo(svc: Service) -> CentrifugoPublisher:
    publisher = real_publisher()
    svc.container.dispatcher = PostCommitDispatcher(publisher)
    return publisher


@respx.mock
async def test_publish_goes_to_personal_channel_with_api_key(notifications: Service) -> None:
    route = respx.post(f"{API}/publish").mock(return_value=httpx.Response(200, json={"result": {}}))
    publisher = await use_centrifugo(notifications)
    user = uuid7()
    await notifications.notify(user, new_level=4)
    await publisher.aclose()

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "a" * 40
    sent = httpx.Response(200, content=request.content).json()
    assert sent["channel"] == f"personal:#{user}"
    assert sent["data"]["notification"]["type"] == "level_up"


@pytest.mark.parametrize(
    "mock",
    [
        pytest.param(lambda route: route.mock(side_effect=httpx.ConnectError("down")), id="down"),
        pytest.param(lambda route: route.mock(side_effect=httpx.ReadTimeout("slow")), id="timeout"),
        pytest.param(lambda route: route.mock(return_value=httpx.Response(503)), id="http-503"),
        pytest.param(
            lambda route: route.mock(
                return_value=httpx.Response(200, json={"error": {"code": 102, "message": "x"}})
            ),
            id="api-error",
        ),
        pytest.param(
            lambda route: route.mock(return_value=httpx.Response(200, content=b"<html>")),
            id="garbage",
        ),
    ],
)
@respx.mock
async def test_centrifugo_failure_keeps_record_and_does_not_fail_consumer(
    notifications: Service, mock: Any
) -> None:
    mock(respx.post(f"{API}/publish"))
    publisher = await use_centrifugo(notifications)
    user = uuid7()
    await notifications.notify(user, new_level=4)  # notify проверяет Outcome.PROCESSED
    await publisher.aclose()

    assert await notifications.count(notifications_table) == 1
    assert notifications.dead_letters.letters == []
    # клиент догоняет списком
    response = await notifications.client.get("/v1/notifications", headers=notifications.auth(user))
    assert len(response.json()["items"]) == 1


async def test_parallel_events_with_different_event_ids_create_one_notification(
    notifications: Service,
) -> None:
    svc, user = notifications, uuid7()
    events = [
        svc.event("progression.ProgressionLevelChanged", user_id=str(user), new_level=6)
        for _ in range(10)
    ]
    assert len({e.event_id for e in events}) == 10
    await asyncio.gather(*(svc.deliver(e) for e in events))
    assert await svc.count(notifications_table) == 1
    assert len(svc.realtime.published) == 1
    assert svc.dead_letters.letters == []


async def test_limiter_counts_in_window_and_sets_expiry(notifications: Service) -> None:
    redis = notifications.infra.redis
    assert redis is not None
    limiter = RedisAttemptLimiter(
        lambda: redis, window_s=30, retry_after_s=2, prefix="notifications:unsub:test-"
    )
    subject = f"203.0.113.{uuid7().int % 250}"
    try:
        assert [await limiter.hit(subject) for _ in range(3)] == [1, 2, 3]
        [key] = [k async for k in redis.scan_iter(match="notifications:unsub:test-*")]
        ttl = await redis.ttl(key)
        assert 0 < ttl <= 30
        assert subject.encode() not in key  # адрес в ключе не хранится открытым
    finally:
        async for key in redis.scan_iter(match="notifications:unsub:test-*"):
            await redis.delete(key)


async def test_limiter_without_redis_fails_closed() -> None:
    limiter = RedisAttemptLimiter(lambda: None, window_s=30, retry_after_s=7)
    with pytest.raises(DependencyUnavailableError) as raised:
        await limiter.hit("1.2.3.4")
    assert raised.value.details == {"retry_after": 7}
