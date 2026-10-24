"""Заглушки social 5c (шаг 0a): доступ по контракту, до потоков F1-F4 - 501."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from social_service.main import DEFINITION
from social_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

UID = "0192a3b4-c5d6-7e8f-9a0b-0000000b0002"
OBJ = "0192a3b4-c5d6-7e8f-9a0b-0000000c0001"
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
REASON = "проверка контракта"
ISSUER = TestTokenIssuer()

PUBLIC_USER: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/me/friends", {}),
    ("DELETE", f"/v1/me/friends/{UID}", {}),
    ("GET", "/v1/me/friend-requests", {}),
    ("POST", "/v1/me/friend-requests", {"json": {"nickname": "Bob"}, "headers": KEY}),
    ("POST", f"/v1/me/friend-requests/{OBJ}/accept", {"headers": KEY}),
    ("POST", f"/v1/me/friend-requests/{OBJ}/decline", {"headers": KEY}),
    ("DELETE", f"/v1/me/friend-requests/{OBJ}", {}),
    ("GET", "/v1/me/friend-code", {}),
    ("POST", "/v1/me/friend-code/regenerate", {"headers": KEY}),
    ("GET", "/v1/me/blocks", {}),
    ("PUT", f"/v1/me/blocks/{UID}", {}),
    ("DELETE", f"/v1/me/blocks/{UID}", {}),
    ("GET", "/v1/me/privacy", {}),
    (
        "PUT",
        "/v1/me/privacy",
        {"json": {"profile_public": True, "stats_public": True, "friend_requests": "all"}},
    ),
    ("POST", "/v1/me/presence", {}),
    (
        "POST",
        f"/v1/me/friends/{UID}/invite",
        {"json": {"room_code": "K7M2QX"}, "headers": KEY},
    ),
    ("GET", f"/v1/players/{UID}/relation", {}),
    ("POST", f"/v1/collections/{OBJ}/comments", {"json": {"body": "Класс"}, "headers": KEY}),
    ("PATCH", f"/v1/comments/{OBJ}", {"json": {"body": "Правка"}}),
    ("DELETE", f"/v1/comments/{OBJ}", {}),
    (
        "POST",
        "/v1/reports",
        {
            "json": {"target_kind": "collection", "target_id": OBJ, "reason_code": "spam"},
            "headers": KEY,
        },
    ),
    ("GET", "/v1/me/sanctions", {}),
]
PUBLIC_OPEN: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", f"/v1/collections/{OBJ}/comments", {}),
    ("GET", "/v1/report-reasons", {}),
]
ADMIN: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/admin/v1/cases", {}),
    ("GET", f"/admin/v1/cases/{OBJ}", {}),
    ("POST", f"/admin/v1/cases/{OBJ}/claim", {"json": {"version": 1}, "headers": KEY}),
    (
        "POST",
        f"/admin/v1/cases/{OBJ}/escalate",
        {"json": {"version": 1, "reason": REASON}, "headers": KEY},
    ),
    (
        "POST",
        f"/admin/v1/cases/{OBJ}/resolve",
        {"json": {"version": 1, "decision": "dismissed", "reason": REASON}, "headers": KEY},
    ),
    ("GET", f"/admin/v1/users/{UID}/moderation", {}),
    (
        "POST",
        f"/admin/v1/users/{UID}/sanctions",
        {"json": {"kind": "online", "duration_s": 3600, "reason": REASON}, "headers": KEY},
    ),
    (
        "POST",
        f"/admin/v1/sanctions/{OBJ}/revoke",
        {"json": {"reason": REASON}, "headers": KEY},
    ),
]


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    infra = await Infrastructure.open(Settings(environment="ci"), DEFINITION.tables)
    try:
        app = create_app(DEFINITION, infra, jwt_verifier=ISSUER.verifier())
        async with service_client(app) as http:
            yield http
    finally:
        await infra.close()


def _auth(**kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {ISSUER.issue(**kwargs)}"}


def _send(client: httpx.AsyncClient, call: tuple[str, str, dict[str, Any]], **extra: Any) -> Any:
    method, path, options = call
    headers = {**options.get("headers", {}), **extra.get("headers", {})}
    kwargs = {k: v for k, v in options.items() if k != "headers"}
    return client.request(method, path, headers=headers, **kwargs)


@pytest.mark.parametrize("call", PUBLIC_USER + ADMIN, ids=lambda c: f"{c[0]} {c[1][:40]}")
async def test_requires_authentication(
    client: httpx.AsyncClient, call: tuple[str, str, dict[str, Any]]
) -> None:
    assert_problem(await _send(client, call), 401, "auth.unauthenticated")


@pytest.mark.parametrize("call", PUBLIC_USER, ids=lambda c: f"{c[0]} {c[1][:40]}")
async def test_public_stub_answers_501_for_user(
    client: httpx.AsyncClient, call: tuple[str, str, dict[str, Any]]
) -> None:
    response = await _send(client, call, headers=_auth())
    assert_problem(response, 501, "common.not_implemented")


@pytest.mark.parametrize("call", PUBLIC_OPEN, ids=lambda c: f"{c[0]} {c[1][:40]}")
async def test_open_stub_answers_501_without_token(
    client: httpx.AsyncClient, call: tuple[str, str, dict[str, Any]]
) -> None:
    assert_problem(await _send(client, call), 501, "common.not_implemented")


@pytest.mark.parametrize("call", ADMIN, ids=lambda c: f"{c[0]} {c[1][:40]}")
async def test_admin_requires_permission_then_501(
    client: httpx.AsyncClient, call: tuple[str, str, dict[str, Any]]
) -> None:
    assert_problem(await _send(client, call, headers=_auth()), 403, "auth.forbidden")
    perms = ["admin.access", "social.moderate", "social.sanctions.manage"]
    response = await _send(client, call, headers=_auth(perms=perms))
    assert_problem(response, 501, "common.not_implemented")


async def test_friend_request_needs_exactly_one_of_nickname_and_code(
    client: httpx.AsyncClient,
) -> None:
    for body in ({}, {"nickname": "Bob", "code": "ABCD2345"}):
        response = await client.post(
            "/v1/me/friend-requests", json=body, headers={**KEY, **_auth()}
        )
        assert response.status_code == 422


async def test_resolve_requires_sanction_only_for_sanctioned(client: httpx.AsyncClient) -> None:
    headers = {**KEY, **_auth(perms=["admin.access", "social.moderate"])}
    path = f"/admin/v1/cases/{OBJ}/resolve"
    sanction = {"kind": "online", "duration_s": 3600, "reason": REASON}
    bad = [
        {"version": 1, "decision": "sanctioned", "reason": REASON},
        {"version": 1, "decision": "dismissed", "sanction": sanction, "reason": REASON},
    ]
    for body in bad:
        assert (await client.post(path, json=body, headers=headers)).status_code == 422


async def test_openapi_has_social_paths_and_no_text_in_events(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    for path in ("/v1/me/friends", "/v1/reports", "/admin/v1/cases", "/v1/me/privacy"):
        assert path in spec["paths"]
    privacy = spec["components"]["schemas"]["PrivacySettings"]["properties"]
    assert set(privacy) == {"profile_public", "stats_public", "friend_requests"}
