"""Заглушки 5d (шаг 0a, ADR 0054-0056): доступ по контракту, до потока E1 - 501."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from progression_service.main import DEFINITION
from progression_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

RUN = "0192a3b4-c5d6-7e8f-9a0b-0000000c0002"
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
REASON = {"reason": "проверка контракта"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
BODY = {
    "title": "Осень",
    "description": "",
    "theme_key": "autumn",
    "finds": [
        {
            "key": "a",
            "number": 1,
            "title": "A",
            "diary_before": "",
            "diary_after": "",
            "x_pct": 1,
            "y_pct": 1,
            "reward": {"xp": 1, "verses": 1},
            "final": False,
            "trigger": {"type": "ui_spot", "page_key": "home"},
        },
        {
            "key": "b",
            "number": 2,
            "title": "B",
            "diary_before": "",
            "diary_after": "",
            "x_pct": 2,
            "y_pct": 2,
            "reward": {"xp": 1, "verses": 1},
            "final": True,
            "trigger": {"type": "ui_spot", "page_key": "shop"},
        },
    ],
    "reward": {"item_key": None},
}
PERIOD = {"starts_at": "2026-11-01T00:00:00Z", "ends_at": "2026-11-08T00:00:00Z", **REASON}

# Вход нужен всем, кроме /events/active.
PUBLIC_STUBS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/events/autumn", {}),
    ("GET", "/v1/me/events", {}),
    ("POST", "/v1/events/autumn/finds/first_leaf/claim", {}),
]
MANAGE = "progression.events.manage"
PUBLISH = "progression.events.publish"
ADMIN_STUBS: list[tuple[str, str, dict[str, Any], str]] = [
    ("GET", "/admin/v1/events", {}, MANAGE),
    ("POST", "/admin/v1/events", {"json": {"key": "autumn", "body": BODY, **REASON}}, MANAGE),
    ("GET", "/admin/v1/events/autumn", {}, MANAGE),
    ("PUT", "/admin/v1/events/autumn", {"json": {"body": BODY, **REASON}}, MANAGE),
    (
        "PUT",
        "/admin/v1/events/autumn/map",
        {"data": REASON, "files": {"file": ("m.png", PNG, "image/png")}},
        MANAGE,
    ),
    ("POST", "/admin/v1/events/autumn/runs", {"json": PERIOD}, MANAGE),
    (
        "POST",
        f"/admin/v1/events/autumn/runs/{RUN}/publish",
        {"json": {"body_sha256": "0" * 64, **REASON}},
        PUBLISH,
    ),
    ("POST", f"/admin/v1/events/autumn/runs/{RUN}/stop", {"json": REASON}, PUBLISH),
    ("GET", f"/admin/v1/events/autumn/runs/{RUN}/stats", {}, MANAGE),
]


@pytest.fixture
async def stub_client() -> AsyncIterator[tuple[httpx.AsyncClient, TestTokenIssuer]]:
    issuer = TestTokenIssuer()
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        app = create_app(DEFINITION, infra, jwt_verifier=issuer.verifier())
        async with service_client(app) as client:
            yield client, issuer
    finally:
        await infra.close()


async def test_active_event_is_anonymous_and_answers_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    client, _ = stub_client
    assert_problem(await client.get("/v1/events/active"), 501, "common.not_implemented")


@pytest.mark.parametrize(("method", "path", "kwargs"), PUBLIC_STUBS)
async def test_public_event_stubs_need_account_and_answer_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    client, issuer = stub_client

    async def call(token: str | None) -> httpx.Response:
        headers = dict(KEY)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await client.request(method, path, headers=headers, **kwargs)

    assert_problem(await call(None), 401, "auth.unauthenticated")
    assert_problem(await call(issuer.issue_guest()), 403, "auth.guest_forbidden")
    assert_problem(await call(issuer.issue()), 501, "common.not_implemented")


@pytest.mark.parametrize(("method", "path", "kwargs", "perm"), ADMIN_STUBS)
async def test_admin_event_stubs_need_specific_permission_and_answer_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    kwargs: dict[str, Any],
    perm: str,
) -> None:
    client, issuer = stub_client

    async def call(token: str | None) -> httpx.Response:
        headers = dict(KEY)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await client.request(method, path, headers=headers, **kwargs)

    assert_problem(await call(None), 401, "auth.unauthenticated")
    for token in (
        issuer.issue(),
        issuer.issue(perms=["admin.access"]),
        issuer.issue(perms=[perm]),
    ):
        assert_problem(await call(token), 403, "auth.forbidden")
    assert_problem(
        await call(issuer.issue(perms=["admin.access", perm])), 501, "common.not_implemented"
    )


async def test_publish_needs_publish_permission_not_manage(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    client, issuer = stub_client
    headers = {"Authorization": f"Bearer {issuer.issue(perms=['admin.access', MANAGE])}", **KEY}
    body = {"body_sha256": "0" * 64, **REASON}
    response = await client.post(
        f"/admin/v1/events/autumn/runs/{RUN}/publish", headers=headers, json=body
    )
    assert_problem(response, 403, "auth.forbidden")


async def test_event_definition_body_rejects_extra_fields(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    client, issuer = stub_client
    headers = {"Authorization": f"Bearer {issuer.issue(perms=['admin.access', MANAGE])}", **KEY}
    body = {"key": "autumn", "body": {**BODY, "secret": 1}, **REASON}
    response = await client.post("/admin/v1/events", headers=headers, json=body)
    assert response.status_code == 422
