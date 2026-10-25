"""HTTP-список активных пользователей identity: курсор, токен, ошибки, разбор ответа."""

from __future__ import annotations

import httpx
import pytest
import respx
from notifications_service.application.ports import (
    DIRECTORY_FORBIDDEN,
    DIRECTORY_UNAVAILABLE,
    DIRECTORY_UNCONFIGURED,
)
from notifications_service.infrastructure.email import HttpUserDirectory, UnconfiguredUserDirectory

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

BASE = "http://identity.test"
URL = f"{BASE}/internal/v1/users/active"


class FakeTokens:
    def __init__(self) -> None:
        self.invalidated = 0
        self.issued = 0

    async def token(self) -> str:
        self.issued += 1
        return f"tok-{self.issued}"

    def invalidate(self) -> None:
        self.invalidated += 1


def directory(tokens: FakeTokens | None = None) -> HttpUserDirectory:
    return HttpUserDirectory(BASE, tokens or FakeTokens(), timeout_s=1.0)


@respx.mock
async def test_page_passes_cursor_and_limit_and_parses_ids() -> None:
    first, second, cursor = uuid7(), uuid7(), uuid7()
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200, json={"items": [str(first), str(second)], "next_cursor": str(second)}
        )
    )
    page = await directory().active_after(cursor, 2)
    assert (page.ids, page.next_cursor) == ([first, second], second)
    request = route.calls.last.request
    assert request.url.params["after"] == str(cursor)
    assert request.url.params["limit"] == "2"
    assert request.headers["authorization"] == "Bearer tok-1"


@respx.mock
async def test_first_page_has_no_cursor_and_last_page_ends() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(200, json={"items": [], "next_cursor": None})
    )
    page = await directory().active_after(None, 10)
    assert (page.ids, page.next_cursor) == ([], None)
    assert "after" not in route.calls.last.request.url.params


@respx.mock
async def test_401_refreshes_token_once() -> None:
    tokens = FakeTokens()
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"items": [], "next_cursor": None}),
        ]
    )
    await directory(tokens).active_after(None, 5)
    assert route.call_count == 2 and tokens.invalidated == 1
    assert route.calls.last.request.headers["authorization"] == "Bearer tok-2"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
@respx.mock
async def test_temporary_statuses_are_retryable(status: int) -> None:
    respx.get(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(DependencyUnavailableError) as err:
        await directory().active_after(None, 5)
    assert err.value.code == DIRECTORY_UNAVAILABLE


@pytest.mark.parametrize("status", [400, 403, 404, 422])
@respx.mock
async def test_other_4xx_is_permanent(status: int) -> None:
    respx.get(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(DependencyUnavailableError) as err:
        await directory().active_after(None, 5)
    assert err.value.code == DIRECTORY_FORBIDDEN


@respx.mock
async def test_network_error_is_retryable() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(DependencyUnavailableError) as err:
        await directory().active_after(None, 5)
    assert err.value.code == DIRECTORY_UNAVAILABLE


@pytest.mark.parametrize("body", [{"items": ["x"], "next_cursor": None}, {"items": []}, [1]])
@respx.mock
async def test_unparsable_body_is_retryable(body: object) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(DependencyUnavailableError) as err:
        await directory().active_after(None, 5)
    assert err.value.code == DIRECTORY_UNAVAILABLE


async def test_unconfigured_directory_is_permanent() -> None:
    with pytest.raises(DependencyUnavailableError) as err:
        await UnconfiguredUserDirectory().active_after(None, 5)
    assert err.value.code == DIRECTORY_UNCONFIGURED
