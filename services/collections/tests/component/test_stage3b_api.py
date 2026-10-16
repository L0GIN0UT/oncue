"""Шаг 0a этапа 3b: контракт API collections объявлен (501), права и схемы уже работают."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from collections_service.main import DEFINITION
from collections_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

CID = "0192a3b4-c5d6-7e8f-9a0b-000000000c01"
ITEM = "0192a3b4-c5d6-7e8f-9a0b-000000000100"
USER = "0192a3b4-c5d6-7e8f-9a0b-000000000001"
KEY = {"Idempotency-Key": "test-key-0001"}
BODY = {"title": "Кино · кадры", "items": [{"media_item_id": ITEM, "custom": {"emoji": "🚢"}}]}

USER_ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("POST", "/v1/collections", {"domain": "film", "title": "Кино · кадры"}),
    ("GET", "/v1/me/collections?status=draft", None),
    ("GET", f"/v1/collections/{CID}/draft", None),
    ("PUT", f"/v1/collections/{CID}/draft", {"base_revision": 1, "body": BODY}),
    ("POST", f"/v1/collections/{CID}/publish", {"draft_revision": 2}),
    ("DELETE", f"/v1/collections/{CID}", None),
    ("PUT", f"/v1/collections/{CID}/like", None),
    ("DELETE", f"/v1/collections/{CID}/like", None),
    ("PUT", f"/v1/collections/{CID}/favorite", None),
    ("DELETE", f"/v1/collections/{CID}/favorite", None),
    ("GET", "/v1/me/favorites", None),
]
PUBLIC_ROUTES = [
    "/v1/collections?q=кадры&domain=film&sort=new&limit=50",
    f"/v1/collections/{CID}",
]
ADMIN_ROUTES: list[tuple[str, str, dict[str, object] | None, str]] = [
    ("GET", "/admin/v1/collections?status=hidden&kind=ugc", None, "collections.view"),
    ("GET", f"/admin/v1/collections/{CID}", None, "collections.view"),
    (
        "POST",
        "/admin/v1/collections",
        {
            "domain": "film",
            "title": "Лучшее 90-х",
            "default_mode_key": "film.collection.frame",
            "items": [{"media_item_id": ITEM}],
            "reason": "кураторский",
        },
        "collections.curate",
    ),
    (
        "PUT",
        f"/admin/v1/collections/{CID}",
        {
            "base_version": 1,
            "title": "Лучшее 90-х",
            "default_mode_key": "film.collection.frame",
            "items": [{"media_item_id": ITEM}],
            "reason": "правка",
        },
        "collections.curate",
    ),
    ("POST", f"/admin/v1/collections/{CID}/hide", {"reason": "спам"}, "collections.unpublish"),
    ("POST", f"/admin/v1/collections/{CID}/unhide", {"reason": "ошибка"}, "collections.unpublish"),
    (
        "PUT",
        f"/admin/v1/collections/{CID}/featured",
        {"featured": True, "rank": 1, "reason": "подборка недели"},
        "collections.curate",
    ),
    (
        "POST",
        f"/admin/v1/authors/{USER}/restriction",
        {"reason": "спам"},
        "collections.restrict_author",
    ),
    (
        "DELETE",
        f"/admin/v1/authors/{USER}/restriction",
        {"reason": "исправился"},
        "collections.restrict_author",
    ),
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


@pytest.mark.parametrize(("method", "path", "body"), USER_ROUTES)
async def test_stage3b_user_routes_are_declared_but_not_implemented(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """Пользователь проходит схему и права — дальше 501 (поток K)."""
    headers = _bearer(token_issuer.issue()) | KEY
    response = await client.request(method, path, json=body, headers=headers)
    assert_problem(response, 501, "common.not_implemented")


@pytest.mark.parametrize(("method", "path", "body"), USER_ROUTES)
async def test_stage3b_user_routes_reject_anonymous_and_guest(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """D7: гость не создаёт сборники, не лайкает и не ведёт избранное."""
    response = await client.request(method, path, json=body, headers=KEY)
    assert_problem(response, 401, "auth.unauthenticated")
    guest = _bearer(token_issuer.issue_guest()) | KEY
    response = await client.request(method, path, json=body, headers=guest)
    assert_problem(response, 403, "auth.guest_forbidden")


@pytest.mark.parametrize("path", PUBLIC_ROUTES)
async def test_stage3b_public_reads_need_no_subject(client: httpx.AsyncClient, path: str) -> None:
    assert_problem(await client.get(path), 501, "common.not_implemented")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/collections", {"domain": "film", "title": "x", "kind": "curated"}),
        ("POST", "/v1/collections", {"domain": "film", "title": "x", "author_id": USER}),
        ("POST", "/v1/collections", {"domain": "anime", "title": "x"}),
        ("POST", "/v1/collections", {"domain": "film", "title": ""}),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "featured": True}},
        ),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "status": "published"}},
        ),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "items": [{"media_item_id": ITEM}] * 201}},
        ),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "default_mode_key": "film.random.frame"}},
        ),
        ("POST", f"/v1/collections/{CID}/publish", {"draft_revision": 0}),
    ],
)
async def test_stage3b_public_schemas_forbid_mass_assignment(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object],
) -> None:
    """§12 п. 2: `kind`, `author_id`, `featured`, `status` из публичного API не принимаются."""
    headers = _bearer(token_issuer.issue()) | KEY
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status_code == 422, response.text


async def test_stage3b_mutations_require_idempotency_key(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    response = await client.put(
        f"/v1/collections/{CID}/like", headers=_bearer(token_issuer.issue())
    )
    assert_problem(response, 422, "idempotency.key_required")


async def test_stage3b_cookie_mutation_requires_csrf(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    client.cookies.set("gv_access", token_issuer.issue())
    response = await client.put(f"/v1/collections/{CID}/like", headers=KEY)
    assert response.status_code == 403, response.text
    client.cookies.clear()


@pytest.mark.parametrize(("method", "path", "body", "perm"), ADMIN_ROUTES)
async def test_stage3b_admin_routes_require_specific_perm(
    *,
    client: httpx.AsyncClient,
    token_issuer: TestTokenIssuer,
    method: str,
    path: str,
    body: dict[str, object] | None,
    perm: str,
) -> None:
    """`admin.access` + право из миграции identity 0007; дальше 501 (поток K)."""
    for token in (
        token_issuer.issue(perms=["admin.access"]),
        token_issuer.issue(perms=[perm]),
        token_issuer.issue_guest(),
    ):
        response = await client.request(method, path, json=body, headers=_bearer(token) | KEY)
        assert_problem(response, 403, "auth.forbidden")
    token = token_issuer.issue(perms=["admin.access", perm])
    response = await client.request(method, path, json=body, headers=_bearer(token) | KEY)
    assert_problem(response, 501, "common.not_implemented")


async def test_stage3b_meta_keeps_stub_shape(client: httpx.AsyncClient) -> None:
    """До потока K `GET /v1/meta` отвечает как заглушка этапа 0 (словари — null)."""
    assert (await client.get("/v1/meta")).json() == {"service": "collections", "status": "stub"}
