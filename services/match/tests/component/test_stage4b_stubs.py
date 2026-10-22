"""Заглушки 0a этапа 4b: очередь и наблюдение — 501 после авторизации (поток Q заменит их).

Авторизация всех ручек (401 без токена, 403 гостю) проверяет ``test_auth.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from match_service.main import DEFINITION
from match_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

IDEMPOTENCY = {"Idempotency-Key": "idem-00000001"}


@pytest.fixture
async def client(token_issuer: TestTokenIssuer) -> AsyncIterator[httpx.AsyncClient]:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    app = create_app(DEFINITION, infra, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as http:
            yield http
    finally:
        await infra.close()


@pytest.mark.parametrize(
    ("method", "path", "body", "headers"),
    [
        ("POST", "/v1/queue/enter", {"ladder": "film", "format": "lite"}, IDEMPOTENCY),
        ("POST", "/v1/queue/tick", None, {}),
        ("POST", "/v1/queue/leave", None, IDEMPOTENCY),
        ("GET", "/v1/queue/pool", None, {}),
    ],
)
async def test_queue_stubs_are_501_for_user(  # noqa: PLR0917
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, str] | None,
    headers: dict[str, str],
) -> None:
    response = await client.request(
        method,
        path,
        json=body,
        headers={**headers, "Authorization": f"Bearer {token_issuer.issue()}"},
    )
    assert_problem(response, 501, "common.not_implemented")


async def test_queue_enter_rejects_unknown_format(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    response = await client.post(
        "/v1/queue/enter",
        json={"ladder": "film", "format": "party"},
        headers={**IDEMPOTENCY, "Authorization": f"Bearer {token_issuer.issue()}"},
    )
    assert response.status_code == 422


async def test_spectate_needs_permission(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    plain = await client.get(
        "/v1/rooms/spectate", headers={"Authorization": f"Bearer {token_issuer.issue()}"}
    )
    assert_problem(plain, 403, "auth.forbidden")
    allowed = await client.get(
        "/v1/rooms/spectate",
        headers={"Authorization": f"Bearer {token_issuer.issue(perms=['match.spectate'])}"},
    )
    assert_problem(allowed, 501, "common.not_implemented")
