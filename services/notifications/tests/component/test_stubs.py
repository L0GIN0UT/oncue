"""Заглушки notifications 5b (шаг 0a, ADR 0047): доступ по контракту, до потока S2 - 501."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from notifications_service.main import DEFINITION
from notifications_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

ID = "0192a3b4-c5d6-7e8f-9a0b-0000000c0001"
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
PUBLIC_STUBS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/notifications", {}),
    ("GET", "/v1/notifications/unread-count", {}),
    ("POST", "/v1/notifications/read-all", {}),
    ("POST", f"/v1/notifications/{ID}/read", {}),
    ("GET", "/v1/notification-settings", {}),
    ("PUT", "/v1/notification-settings/level_up", {"json": {"in_app": True, "email": False}}),
    ("GET", "/v1/email-subscription", {}),
    ("PUT", "/v1/email-subscription", {"json": {"subscribed": True}}),
    ("POST", "/v1/devices", {"json": {"platform": "web", "token": "x" * 16}}),
    ("DELETE", f"/v1/devices/{ID}", {}),
]
BROADCAST = {"subject": "Новости", "body": "Текст", "reason": "проверка контракта"}
PERM = "notifications.broadcast.send"


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


@pytest.mark.parametrize(("method", "path", "kwargs"), PUBLIC_STUBS)
async def test_public_stubs_need_account_and_answer_501(
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


async def test_unsubscribe_needs_no_login(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    """Отписка по ссылке из письма: без входа (токен подписан сервером)."""
    client, _ = stub_client
    response = await client.post("/v1/unsubscribe", json={"token": "x" * 32})
    assert_problem(response, 501, "common.not_implemented")


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/admin/v1/broadcasts", {}),
        ("POST", "/admin/v1/broadcasts", {"json": BROADCAST}),
    ],
)
async def test_broadcast_stubs_need_specific_permission_and_answer_501(
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
    for token in (
        issuer.issue(),
        issuer.issue(perms=["admin.access"]),
        issuer.issue(perms=[PERM]),
    ):
        assert_problem(await call(token), 403, "auth.forbidden")
    allowed = await call(issuer.issue(perms=["admin.access", PERM]))
    assert_problem(allowed, 501, "common.not_implemented")
