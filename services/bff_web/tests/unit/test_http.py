"""HTTP-слой bff: circuit breaker, разбор ответов источников, проброс контекста."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx
from bff_web_service.application.ports import (
    TRACE_HEADERS,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.infrastructure.clients import HttpCatalogClient
from bff_web_service.infrastructure.http import BreakerState, CircuitBreaker, UpstreamHttp

from gv_core.context import RequestContext, bind_context

pytestmark = pytest.mark.unit

BASE = "http://catalog.test"
REQUEST_ID = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000ff")
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self.data[key], self.ttl[key] = value, ttl_s


def state(breaker: CircuitBreaker) -> BreakerState:
    """Состояние через функцию: mypy не «запоминает» значение свойства между шагами."""
    return breaker.state


def test_breaker_opens_after_failures_and_probes_once() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failures=2, reset_s=10, clock=clock)
    breaker.failure()
    assert breaker.allow()
    breaker.failure()
    assert state(breaker) is BreakerState.OPEN
    assert not breaker.allow()

    clock.now = 10
    assert state(breaker) is BreakerState.HALF_OPEN
    assert breaker.allow()  # пробный вызов
    assert not breaker.allow()  # второй параллельный — нет
    breaker.failure()
    assert state(breaker) is BreakerState.OPEN  # проба упала — новое окно

    clock.now = 20
    assert breaker.allow()
    breaker.success()
    assert state(breaker) is BreakerState.CLOSED
    assert breaker.allow()


def test_breaker_rejected_probe_releases_slot() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failures=1, reset_s=1, clock=clock)
    breaker.failure()
    clock.now = 1
    assert breaker.allow()
    breaker.release()  # 4xx — не вердикт о здоровье
    assert breaker.allow()


def test_breaker_validates_config() -> None:
    with pytest.raises(ValueError, match="failures"):
        CircuitBreaker(failures=0, reset_s=1)


def make_http(
    client: httpx.AsyncClient, *, failures: int = 3, cache: MemoryCache | None = None
) -> UpstreamHttp:
    breaker = CircuitBreaker(failures=failures, reset_s=60)
    return UpstreamHttp("catalog", client, breaker, timeout_s=0.5, cache=cache)


async def test_forwards_bearer_request_id_and_trace() -> None:
    async with httpx.AsyncClient(base_url=BASE) as client:
        http = make_http(client)
        with respx.mock() as mock:
            route = mock.get(f"{BASE}/v1/me").respond(200, json={})
            TRACE_HEADERS.set({"traceparent": TRACEPARENT})
            with bind_context(RequestContext(correlation_id=REQUEST_ID)):
                await http.get("/v1/me", token="tkn")
        sent = route.calls.last.request.headers
        assert sent["authorization"] == "Bearer tkn"
        assert sent["x-request-id"] == str(REQUEST_ID)
        assert sent["traceparent"] == TRACEPARENT


async def test_no_authorization_without_token() -> None:
    async with httpx.AsyncClient(base_url=BASE) as client:
        with respx.mock() as mock:
            route = mock.get(f"{BASE}/v1/modes").respond(200, json=[])
            await make_http(client).get("/v1/modes", token=None)
        assert "authorization" not in route.calls.last.request.headers


async def test_client_error_is_rejection_with_problem_code() -> None:
    async with httpx.AsyncClient(base_url=BASE) as client:
        http = make_http(client, failures=1)
        with respx.mock() as mock:
            mock.get(f"{BASE}/v1/x").respond(
                404, json={"code": "game.session_not_found", "status": 404, "details": {"a": 1}}
            )
            with pytest.raises(UpstreamRejectedError) as caught:
                await http.get("/v1/x", token=None)
            # 4xx не размыкают цепь: следующий вызов идёт в источник.
            mock.get(f"{BASE}/v1/y").respond(400, text="not json")
            with pytest.raises(UpstreamRejectedError) as fallback:
                await http.get("/v1/y", token=None)
    assert (caught.value.status, caught.value.code, caught.value.details) == (
        404,
        "game.session_not_found",
        {"a": 1},
    )
    assert fallback.value.code == "http.400"


@pytest.mark.parametrize("status", [500, 503, 429])
async def test_server_errors_and_throttling_are_unavailable_and_open_circuit(status: int) -> None:
    async with httpx.AsyncClient(base_url=BASE) as client:
        http = make_http(client, failures=2)
        with respx.mock() as mock:
            route = mock.get(f"{BASE}/v1/x").respond(status)
            for _ in range(2):
                with pytest.raises(UpstreamUnavailableError, match=f"http_{status}"):
                    await http.get("/v1/x", token=None)
            with pytest.raises(UpstreamUnavailableError, match="circuit_open"):
                await http.get("/v1/x", token=None)
    assert route.call_count == 2  # третий вызов не дошёл до упавшего сервиса


async def test_timeout_and_network_errors_are_unavailable() -> None:
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with httpx.AsyncClient(base_url=BASE) as client:
        http = make_http(client)
        # Отменённый по таймауту вызов respx не засчитывает как «вызванный».
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{BASE}/slow").mock(side_effect=slow)
            mock.get(f"{BASE}/down").mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(UpstreamUnavailableError, match="timeout"):
                await http.get("/slow", token=None)
            with pytest.raises(UpstreamUnavailableError, match="ConnectError"):
                await http.get("/down", token=None)


ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a1")
CARD: dict[str, Any] = {
    "id": str(ITEM),
    "domain": "film",
    "kind": "movie",
    "title": "Брат",
    "original_title": None,
    "year": 1997,
    "subtitle": None,
    "genres": ["drama"],
    "persons": [{"person_id": str(ITEM), "name": "А. Балабанов", "role": "director", "order": 0}],
    "metrics": {},
}


async def test_catalog_card_cached_only_after_valid_parse() -> None:
    cache = MemoryCache()
    async with httpx.AsyncClient(base_url=BASE) as client:
        catalog = HttpCatalogClient(
            make_http(client, cache=cache), item_ttl_s=300, cache_prefix="t:"
        )
        with respx.mock() as mock:
            route = mock.get(f"{BASE}/v1/items/{ITEM}")
            route.respond(200, json={"id": "broken"})
            with pytest.raises(UpstreamUnavailableError, match="contract_violation"):
                await catalog.get_item(ITEM)
            assert cache.data == {}

            route.respond(200, json=CARD)
            first = await catalog.get_item(ITEM)
            second = await catalog.get_item(ITEM)
    assert first == second
    assert first.persons[0].name == "А. Балабанов"
    assert route.call_count == 2  # третий раз — из кэша
    assert cache.ttl == {f"t:catalog:item:v1:{ITEM}": 300}


async def test_catalog_card_of_another_item_is_not_cached() -> None:
    cache = MemoryCache()
    other = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000b2")
    async with httpx.AsyncClient(base_url=BASE) as client:
        catalog = HttpCatalogClient(make_http(client, cache=cache), item_ttl_s=300, cache_prefix="")
        with respx.mock() as mock:
            mock.get(f"{BASE}/v1/items/{other}").respond(200, json=CARD)
            with pytest.raises(UpstreamUnavailableError):
                await catalog.get_item(other)
    assert cache.data == {}
