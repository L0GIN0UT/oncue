"""Отзыв подписки Centrifugo с повтором и метрикой (хвост 4a, ADR 0052)."""

from __future__ import annotations

import httpx
import pytest
from match_service.infrastructure.realtime import (
    DISCONNECT_FAILED,
    REALTIME_PUBLISH,
    UNSUBSCRIBE_FAILED,
    CentrifugoPublisher,
)
from pydantic import SecretStr

from gv_core.ids import uuid7
from gv_service import RealtimeSettings
from gv_testkit import stub_service

pytestmark = pytest.mark.component

CENTRIFUGO = "http://centrifugo.test/api"


def _publisher(attempts: int, sleeps: list[float]) -> CentrifugoPublisher:
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return CentrifugoPublisher(
        RealtimeSettings(api_url=CENTRIFUGO, api_key=SecretStr("k" * 40)),
        unsubscribe_attempts=attempts,
        unsubscribe_retry_s=0.5,
        sleep=sleep,
    )


def _ok() -> float:
    return REALTIME_PUBLISH.labels("unsubscribe_ok")._value.get()


async def test_transient_failure_is_retried_until_success() -> None:
    sleeps: list[float] = []
    publisher = _publisher(3, sleeps)
    failed_before, ok_before = UNSUBSCRIBE_FAILED._value.get(), _ok()
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/unsubscribe")
            route.side_effect = [
                httpx.ConnectError("down"),
                httpx.Response(503),
                httpx.Response(200, json={"result": {}}),
            ]
            await publisher.unsubscribe(uuid7(), "room:K7MQ2ZXA")
            assert route.call_count == 3
    finally:
        await publisher.aclose()
    assert sleeps == [0.5, 1.0]  # линейная пауза
    assert _ok() == ok_before + 1
    assert UNSUBSCRIBE_FAILED._value.get() == failed_before


async def test_giving_up_counts_metric_and_does_not_raise() -> None:
    sleeps: list[float] = []
    publisher = _publisher(3, sleeps)
    failed_before = UNSUBSCRIBE_FAILED._value.get()
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/unsubscribe")
            route.respond(200, json={"error": {"code": 100, "message": "internal"}})
            await publisher.unsubscribe(uuid7(), "room:K7MQ2ZXA")
            assert route.call_count == 3
    finally:
        await publisher.aclose()
    assert UNSUBSCRIBE_FAILED._value.get() == failed_before + 1
    assert len(sleeps) == 2  # после последней попытки не ждём


async def test_single_attempt_by_default() -> None:
    publisher = CentrifugoPublisher(
        RealtimeSettings(api_url=CENTRIFUGO, api_key=SecretStr("k" * 40))
    )
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/unsubscribe")
            route.respond(503)
            await publisher.unsubscribe(uuid7(), "room:K7MQ2ZXA")
            assert route.call_count == 1
    finally:
        await publisher.aclose()


def test_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CentrifugoPublisher(
            RealtimeSettings(api_url=CENTRIFUGO, api_key=SecretStr("k" * 40)),
            unsubscribe_attempts=0,
        )


@pytest.mark.parametrize(("reconnect", "code"), [(True, 3000), (False, 3500)])
async def test_disconnect_request_shape(reconnect: bool, code: int) -> None:
    import json

    publisher = _publisher(1, [])
    user = uuid7()
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/disconnect")
            route.respond(200, json={"result": {}})
            await publisher.disconnect(user, reconnect=reconnect, reason="kicked")
            request = route.calls.last.request
    finally:
        await publisher.aclose()
    assert json.loads(request.content) == {
        "user": str(user),
        "disconnect": {"code": code, "reason": "kicked"},
    }
    assert request.headers["X-API-Key"] == "k" * 40


async def test_disconnect_retries_and_counts_giving_up() -> None:
    sleeps: list[float] = []
    publisher = _publisher(3, sleeps)
    before = DISCONNECT_FAILED._value.get()
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/disconnect")
            route.respond(200, json={"error": {"code": 100, "message": "internal"}})
            await publisher.disconnect(uuid7(), reconnect=False, reason="account_banned")
            assert route.call_count == 3
    finally:
        await publisher.aclose()
    assert DISCONNECT_FAILED._value.get() == before + 1
    assert sleeps == [0.5, 1.0]


async def test_disconnect_transient_failure_succeeds() -> None:
    publisher = _publisher(3, [])
    before = DISCONNECT_FAILED._value.get()
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/disconnect")
            route.side_effect = [httpx.ConnectError("down"), httpx.Response(200, json={})]
            await publisher.disconnect(uuid7(), reconnect=True, reason="kicked")
            assert route.call_count == 2
    finally:
        await publisher.aclose()
    assert DISCONNECT_FAILED._value.get() == before
