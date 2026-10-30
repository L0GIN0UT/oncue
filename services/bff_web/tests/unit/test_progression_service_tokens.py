"""bff → progression: уровни игроков идут только под сервисным токеном (internal-ручка)."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest
import respx
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.infrastructure.clients import HttpProgressionClient
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp

from gv_core.errors import DependencyUnavailableError

pytestmark = pytest.mark.unit

BASE = "http://progression.test"
PATH = f"{BASE}/internal/v1/levels"
FIRST = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000901")
SECOND = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000902")


class Tokens:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.invalidated = error, 0

    async def token(self) -> str:
        if self.error is not None:
            raise self.error
        return "service-token"

    def invalidate(self) -> None:
        self.invalidated += 1


def client(tokens: Tokens | None) -> HttpProgressionClient:
    http = UpstreamHttp(
        "progression",
        httpx.AsyncClient(base_url=BASE),
        CircuitBreaker(failures=5, reset_s=10),
        timeout_s=1.0,
    )
    return HttpProgressionClient(http, service_tokens=tokens)


@respx.mock
async def test_levels_use_service_token_and_repeat_user_ids() -> None:
    route = respx.get(PATH).respond(
        200, json={"items": [{"user_id": str(FIRST), "level": 12, "xp_in_level": 40}]}
    )

    levels = await client(Tokens()).levels([FIRST, SECOND])

    assert {u: lv.level for u, lv in levels.items()} == {FIRST: 12}  # SECOND скрыт/не найден
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer service-token"
    assert request.url.params.get_list("user_ids") == [str(FIRST), str(SECOND)]


@respx.mock
async def test_levels_without_service_auth_are_unavailable_and_make_no_call() -> None:
    route = respx.get(PATH).respond(200, json={"items": []})

    with pytest.raises(UpstreamUnavailableError):
        await client(None).levels([FIRST])

    assert route.call_count == 0


@respx.mock
async def test_levels_identity_down_is_unavailable() -> None:
    route = respx.get(PATH).respond(200, json={"items": []})
    down = DependencyUnavailableError("identity down", code="auth.service_token_unavailable")

    with pytest.raises(UpstreamUnavailableError):
        await client(Tokens(down)).levels([FIRST])

    assert route.call_count == 0


@respx.mock
async def test_levels_401_drops_cached_token() -> None:
    respx.get(PATH).respond(401, json={"code": "auth.unauthenticated", "status": 401})
    tokens = Tokens()

    with pytest.raises(UpstreamRejectedError):
        await client(tokens).levels([FIRST])

    assert tokens.invalidated == 1


@respx.mock
async def test_levels_answer_with_foreign_user_is_contract_violation() -> None:
    respx.get(PATH).respond(
        200, json={"items": [{"user_id": str(SECOND), "level": 3, "xp_in_level": 0}]}
    )

    with pytest.raises(UpstreamUnavailableError):
        await client(Tokens()).levels([FIRST])
