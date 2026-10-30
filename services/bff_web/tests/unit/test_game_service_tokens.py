"""bff → game: результаты дейлика идут только под сервисным токеном (internal-ручка)."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest
import respx
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.infrastructure.clients import HttpGameClient
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp

from gv_core.errors import DependencyUnavailableError

pytestmark = pytest.mark.unit

BASE = "http://game.test"
PATH = f"{BASE}/internal/v1/daily/results"
USER = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000901")
ROW = {
    "user_id": str(USER),
    "mode_key": "music.daily.classic",
    "domain": "music",
    "day": "2026-10-10",
    "score": 21,
    "max_score": 30,
}


class Tokens:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.invalidated = error, 0

    async def token(self) -> str:
        if self.error is not None:
            raise self.error
        return "service-token"

    def invalidate(self) -> None:
        self.invalidated += 1


def client(tokens: Tokens | None) -> HttpGameClient:
    http = UpstreamHttp(
        "game",
        httpx.AsyncClient(base_url=BASE),
        CircuitBreaker(failures=5, reset_s=10),
        timeout_s=1.0,
    )
    return HttpGameClient(http, modes_ttl_s=1, cache_prefix="t:", service_tokens=tokens)


@respx.mock
async def test_daily_results_use_service_token_not_viewer_token() -> None:
    route = respx.get(PATH).respond(200, json=[ROW])

    results = await client(Tokens()).daily_results([USER], "music")

    assert [r.score for r in results] == [21]
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer service-token"
    assert request.url.params.get_list("user_ids") == [str(USER)]
    assert request.url.params["domain"] == "music"


@respx.mock
async def test_daily_results_without_service_auth_are_unavailable_and_make_no_call() -> None:
    route = respx.get(PATH).respond(200, json=[])

    with pytest.raises(UpstreamUnavailableError):
        await client(None).daily_results([USER])

    assert route.call_count == 0


@respx.mock
async def test_daily_results_identity_down_is_unavailable() -> None:
    route = respx.get(PATH).respond(200, json=[])
    down = DependencyUnavailableError("identity down", code="auth.service_token_unavailable")

    with pytest.raises(UpstreamUnavailableError):
        await client(Tokens(down)).daily_results([USER])

    assert route.call_count == 0


@respx.mock
async def test_daily_results_401_drops_cached_token() -> None:
    respx.get(PATH).respond(401, json={"code": "auth.unauthenticated", "status": 401})
    tokens = Tokens()

    with pytest.raises(UpstreamRejectedError):
        await client(tokens).daily_results([USER])

    assert tokens.invalidated == 1
