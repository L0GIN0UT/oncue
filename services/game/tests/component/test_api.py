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
    assert (await client.get("/v1/meta")).json() == {"service": "game", "status": "stub"}
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body", "headers"),
    [
        ("GET", "/v1/modes?domain=music", None, {}),
        ("POST", "/v1/sessions", {"mode_key": "music.daily.classic"}, KEY),
        ("GET", f"/v1/sessions/{SESSION}", None, {}),
        ("POST", f"/v1/sessions/{SESSION}/rounds/0/clue", {"level": 1}, {}),
        (
            "POST",
            f"/v1/sessions/{SESSION}/guesses",
            {"round_index": 0, "expected_version": 1, "answer": {"text": "Интерстеллар"}},
            KEY,
        ),
        ("POST", f"/v1/sessions/{SESSION}/skips", {"round_index": 0, "expected_version": 1}, KEY),
        ("POST", f"/v1/sessions/{SESSION}/rounds/0/give-up", {"expected_version": 1}, KEY),
        ("GET", f"/v1/sessions/{SESSION}/result", None, {}),
        ("GET", "/v1/daily", None, {}),
    ],
)
async def test_public_stubs_return_501(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
    headers: dict[str, str],
) -> None:
    auth = {"Authorization": f"Bearer {token_issuer.issue_guest()}", **headers}
    response = await client.request(method, path, json=body, headers=auth)
    assert_problem(response, 501, "common.not_implemented")


async def test_session_routes_require_subject(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get(f"/v1/sessions/{SESSION}"), 401, "auth.unauthenticated")


async def test_moves_require_idempotency_key(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    response = await client.post(
        f"/v1/sessions/{SESSION}/skips",
        json={"round_index": 0, "expected_version": 1},
        headers={"Authorization": f"Bearer {token_issuer.issue()}"},
    )
    assert_problem(response, 422, "idempotency.key_required")


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
    ("method", "path", "perm", "body"),
    [
        ("GET", "/admin/v1/mode-configs", "config.modes.view", None),
        ("GET", "/admin/v1/mode-configs/schema", "config.modes.view", None),
        ("GET", "/admin/v1/mode-configs/music.daily.classic", "config.modes.view", None),
        ("GET", "/admin/v1/mode-configs/music.daily.classic/versions/1", "config.modes.view", None),
        ("POST", "/admin/v1/mode-configs/validate", "config.modes.view", {"body": {}}),
        (
            "PUT",
            "/admin/v1/mode-configs/music.daily.classic/draft",
            "config.modes.edit",
            {"body": {}, "base_revision": None, "reason": "правка очков"},
        ),
        (
            "POST",
            "/admin/v1/mode-configs/music.daily.classic/publish",
            "config.modes.publish",
            {"draft_revision": 1, "reason": "публикация"},
        ),
        (
            "POST",
            "/admin/v1/mode-configs/music.daily.classic/rollback",
            "config.modes.publish",
            {"to_version": 1, "reason": "откат"},
        ),
        (
            "POST",
            "/admin/v1/mode-configs/music.daily.classic/archive",
            "config.modes.publish",
            {"reason": "архив"},
        ),
        (
            "GET",
            "/admin/v1/daily-sets?mode_key=music.daily.classic&from=2026-10-10&to=2026-10-12",
            "game.daily.view",
            None,
        ),
        (
            "PUT",
            "/admin/v1/daily-sets/music.daily.classic/2026-10-11/items/0",
            "game.daily.curate",
            {"media_item_id": SESSION, "reason": "надпись на кадре"},
        ),
        (
            "POST",
            "/admin/v1/daily-sets/music.daily.classic/2026-10-11/regenerate",
            "game.daily.curate",
            {"reason": "перегенерация"},
        ),
        ("GET", "/admin/v1/projection/items?q=инте&domain=film", "game.daily.view", None),
    ],
)
async def test_admin_stubs_check_perm_then_return_501(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    perm: str,
    body: dict[str, object] | None,
) -> None:
    allowed = token_issuer.issue(perms=["admin.access", perm])
    denied = token_issuer.issue(perms=["admin.access"])
    for token, status, code in (
        (denied, 403, "auth.forbidden"),
        (allowed, 501, "common.not_implemented"),
    ):
        headers = {"Authorization": f"Bearer {token}", **KEY}
        response = await client.request(method, path, json=body, headers=headers)
        assert_problem(response, status, code)
