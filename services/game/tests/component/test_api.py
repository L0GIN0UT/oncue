"""Граница API без инфраструктуры: аутентификация, права, обязательные заголовки, схемы."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from game_service.main import DEFINITION
from game_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

SESSION = "0192a3b4-c5d6-7e8f-9a0b-000000000700"
KEY = {"Idempotency-Key": "test-key-0001"}


@pytest.fixture
async def client(token_issuer: TestTokenIssuer) -> AsyncIterator[httpx.AsyncClient]:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    app = create_app(DEFINITION, infra, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as c:
            yield c
    finally:
        await infra.close()


async def test_meta_and_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/meta")
    assert response.json() == {"service": "game", "status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get("/health/live")).status_code == 200


async def test_session_routes_require_subject(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/v1/sessions/{SESSION}")
    assert_problem(response, 401, "auth.unauthenticated")
    assert response.headers["cache-control"] == "no-store"


async def test_moves_require_idempotency_key(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    response = await client.post(
        f"/v1/sessions/{SESSION}/skips",
        json={"round_index": 0, "expected_version": 1},
        headers={"Authorization": f"Bearer {token_issuer.issue()}"},
    )
    assert_problem(response, 422, "idempotency.key_required")


async def test_cookie_mutation_requires_csrf(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    client.cookies.set("gv_access", token_issuer.issue_guest())
    response = await client.post(
        "/v1/sessions", json={"mode_key": "music.daily.classic"}, headers=KEY
    )
    assert response.status_code == 403, response.text
    client.cookies.clear()


async def test_answer_is_item_or_text(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    response = await client.post(
        f"/v1/sessions/{SESSION}/guesses",
        json={"round_index": 0, "expected_version": 1, "answer": {"text": ""}},
        headers={"Authorization": f"Bearer {token_issuer.issue()}", **KEY},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/admin/v1/mode-configs", None),
        ("GET", "/admin/v1/mode-configs/schema", None),
        ("POST", "/admin/v1/mode-configs/validate", {"body": {}}),
        (
            "PUT",
            "/admin/v1/mode-configs/music.daily.classic/draft",
            {"body": {}, "base_revision": None, "reason": "правка очков"},
        ),
        (
            "POST",
            "/admin/v1/mode-configs/music.daily.classic/publish",
            {"draft_revision": 1, "reason": "публикация"},
        ),
        (
            "GET",
            "/admin/v1/daily-sets?mode_key=music.daily.classic&from=2026-10-10&to=2026-10-12",
            None,
        ),
        (
            "PUT",
            "/admin/v1/daily-sets/music.daily.classic/2026-10-11/items/0",
            {"media_item_id": SESSION, "reason": "надпись на кадре"},
        ),
        ("GET", "/admin/v1/projection/items?q=инте&domain=film", None),
    ],
)
async def test_admin_requires_specific_perm(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    for token in (token_issuer.issue(perms=["admin.access"]), token_issuer.issue_guest()):
        headers = {"Authorization": f"Bearer {token}", **KEY}
        response = await client.request(method, path, json=body, headers=headers)
        assert_problem(response, 403, "auth.forbidden")


async def test_schema_endpoint_lists_mode_config(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    token = token_issuer.issue(perms=["admin.access", "config.modes.view"])
    response = await client.get(
        "/admin/v1/mode-configs/schema", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert "properties" in response.json()["mode_config"]
