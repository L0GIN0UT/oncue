"""HTTP-справочник адресов identity: пачки, пропуски, ошибки, токен, отсутствие адресов в логах."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from notifications_service.application.ports import (
    DIRECTORY_FORBIDDEN,
    DIRECTORY_UNAVAILABLE,
    PERMANENT_DIRECTORY_CODES,
)
from notifications_service.infrastructure.email import HttpEmailDirectory
from structlog.testing import capture_logs

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

BASE = "http://identity.test"
URL = f"{BASE}/internal/v1/users/emails"
ADDRESS_MARK = "@secret-mail.test"


class FakeTokens:
    def __init__(self) -> None:
        self.invalidated = 0
        self.issued = 0

    async def token(self) -> str:
        self.issued += 1
        return f"tok-{self.issued}"

    def invalidate(self) -> None:
        self.invalidated += 1


def directory(tokens: FakeTokens | None = None, batch: int = 500) -> HttpEmailDirectory:
    return HttpEmailDirectory(BASE, tokens or FakeTokens(), timeout_s=1.0, batch_size=batch)


def echo(request: httpx.Request) -> httpx.Response:
    ids = json.loads(request.content)["user_ids"]
    items = [{"user_id": i, "email": f"u{n}{ADDRESS_MARK}"} for n, i in enumerate(ids)]
    return httpx.Response(200, json={"items": items})


@respx.mock
async def test_ids_are_split_into_batches_of_at_most_500() -> None:
    route = respx.post(URL).mock(side_effect=echo)
    ids = [uuid7() for _ in range(1201)]
    result = await directory().resolve(ids)
    assert route.call_count == 3
    sizes = [len(json.loads(c.request.content)["user_ids"]) for c in route.calls]
    assert sizes == [500, 500, 201]
    assert len(result) == 1201
    assert {r.user_id for r in result} == set(ids)


@respx.mock
async def test_users_missing_in_response_are_skipped() -> None:
    known, unknown = uuid7(), uuid7()
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"items": [{"user_id": str(known), "email": "a@example.test"}]}
        )
    )
    result = await directory().resolve([known, unknown])
    assert [(r.user_id, r.email) for r in result] == [(known, "a@example.test")]


@respx.mock
async def test_empty_input_makes_no_call() -> None:
    route = respx.post(URL).mock(side_effect=echo)
    assert await directory().resolve([]) == []
    assert route.call_count == 0


@respx.mock
async def test_authorization_comes_from_token_provider() -> None:
    route = respx.post(URL).mock(side_effect=echo)
    await directory(FakeTokens()).resolve([uuid7()])
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-1"


@respx.mock
@pytest.mark.parametrize("status", [403, 400, 404, 422])
async def test_client_errors_are_permanent(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status, json={"code": "x"}))
    with pytest.raises(DependencyUnavailableError) as raised:
        await directory().resolve([uuid7()])
    assert raised.value.code == DIRECTORY_FORBIDDEN
    assert DIRECTORY_FORBIDDEN in PERMANENT_DIRECTORY_CODES


@respx.mock
@pytest.mark.parametrize("status", [500, 502, 503, 429])
async def test_server_errors_and_throttling_are_retryable(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(DependencyUnavailableError) as raised:
        await directory().resolve([uuid7()])
    assert raised.value.code == DIRECTORY_UNAVAILABLE
    assert DIRECTORY_UNAVAILABLE not in PERMANENT_DIRECTORY_CODES


@respx.mock
async def test_network_error_is_retryable() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(DependencyUnavailableError) as raised:
        await directory().resolve([uuid7()])
    assert raised.value.code == DIRECTORY_UNAVAILABLE


@respx.mock
async def test_unauthorized_refreshes_token_once_then_is_permanent() -> None:
    tokens = FakeTokens()
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(DependencyUnavailableError) as raised:
        await directory(tokens).resolve([uuid7()])
    assert raised.value.code == DIRECTORY_FORBIDDEN
    assert route.call_count == 2
    assert tokens.invalidated == 2
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok-2"


@respx.mock
async def test_unauthorized_then_ok_recovers_with_new_token() -> None:
    tokens = FakeTokens()
    respx.post(URL).mock(side_effect=[httpx.Response(401), echo])
    result = await directory(tokens).resolve([uuid7()])
    assert len(result) == 1


@respx.mock
async def test_malformed_response_is_retryable_and_not_leaked() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"items": [{"email": f"x{ADDRESS_MARK}"}]})
    )
    with pytest.raises(DependencyUnavailableError) as raised:
        await directory().resolve([uuid7()])
    assert raised.value.code == DIRECTORY_UNAVAILABLE
    assert ADDRESS_MARK not in repr(raised.value) + str(raised.value)


@respx.mock
async def test_addresses_never_reach_logs_or_errors() -> None:
    respx.post(URL).mock(side_effect=echo)
    with capture_logs() as logs:
        result = await directory().resolve([uuid7(), uuid7()])
    assert result
    assert ADDRESS_MARK not in repr(logs)
    respx.post(URL).mock(return_value=httpx.Response(403, text=f"nope{ADDRESS_MARK}"))
    with capture_logs() as logs, pytest.raises(DependencyUnavailableError) as raised:
        await directory().resolve([uuid7()])
    assert ADDRESS_MARK not in repr(logs) + repr(raised.value) + str(raised.value)
