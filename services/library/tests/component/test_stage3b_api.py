"""Шаг 0a этапа 3b: контракт API library объявлен (501), права и схемы уже работают."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from library_service.main import DEFINITION
from library_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

SHELF = "0192a3b4-c5d6-7e8f-9a0b-000000000d01"
ITEM = "0192a3b4-c5d6-7e8f-9a0b-000000000101"
KEY = {"Idempotency-Key": "test-key-0001"}

ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/v1/shelves", None),
    ("POST", "/v1/shelves", {"title": "Ужасы 80-х"}),
    ("PATCH", f"/v1/shelves/{SHELF}", {"title": "Ужасы 70-х"}),
    ("DELETE", f"/v1/shelves/{SHELF}", None),
    ("GET", "/v1/shelves/system:watch_later/items", None),
    ("GET", f"/v1/shelves/{SHELF}/items?limit=100", None),
    ("PUT", f"/v1/shelves/system:watch_later/items/{ITEM}", {"source": "reveal"}),
    ("PUT", f"/v1/shelves/{SHELF}/items/{ITEM}", {"source": "results"}),
    ("DELETE", f"/v1/shelves/system:watched/items/{ITEM}?source=card", None),
    ("GET", f"/v1/items/{ITEM}/shelves", None),
]


@pytest.fixture
async def client(token_issuer: TestTokenIssuer) -> AsyncIterator[httpx.AsyncClient]:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    app = create_app(DEFINITION, infra, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as c:
            yield c
    finally:
        await infra.close()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_stage3b_routes_are_declared_but_not_implemented(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    headers = _bearer(token_issuer.issue()) | KEY
    response = await client.request(method, path, json=body, headers=headers)
    assert_problem(response, 501, "common.not_implemented")


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_stage3b_library_is_for_users_only(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """D7, D11: медиатека приватна и только у пользователей."""
    response = await client.request(method, path, json=body, headers=KEY)
    assert_problem(response, 401, "auth.unauthenticated")
    guest = _bearer(token_issuer.issue_guest()) | KEY
    response = await client.request(method, path, json=body, headers=guest)
    assert_problem(response, 403, "auth.guest_forbidden")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/shelves/system:Watch-Later/items", None),
        ("GET", "/v1/shelves/123/items", None),
        ("GET", "/v1/shelves/system:/items", None),
        ("PUT", f"/v1/shelves/system:watched/items/{ITEM}", {"source": "elsewhere"}),
        ("PUT", f"/v1/shelves/system:watched/items/{ITEM}", {"source": "reveal", "user_id": ITEM}),
        ("POST", "/v1/shelves", {"title": ""}),
        ("POST", "/v1/shelves", {"title": "Мои", "kind": "system"}),
        ("PATCH", "/v1/shelves/system:watched", {"title": "Мои"}),
    ],
)
async def test_stage3b_shelf_ref_and_bodies_are_strict(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """`shelf_ref` разбирается строго, лишние поля запрещены (mass assignment)."""
    headers = _bearer(token_issuer.issue()) | KEY
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status_code == 422, response.text


async def test_stage3b_cookie_mutation_requires_csrf(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    client.cookies.set("gv_access", token_issuer.issue())
    response = await client.put(
        f"/v1/shelves/system:watched/items/{ITEM}", json={"source": "reveal"}, headers=KEY
    )
    assert response.status_code == 403, response.text
    client.cookies.clear()
