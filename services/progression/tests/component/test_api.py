from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from progression_service.main import DEFINITION
from progression_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component


async def test_meta_and_health() -> None:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        async with service_client(create_app(DEFINITION, infra)) as client:
            assert (await client.get("/v1/meta")).json() == {
                "service": "progression",
                "status": "stub",
            }
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == 200
    finally:
        await infra.close()


# ------------------------------------------------------------------ заглушки 0a (контракт этапа 5a)
UID = "0192a3b4-c5d6-7e8f-9a0b-0000000b0001"
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
REASON = {"reason": "проверка контракта"}
# me/progress, me/wallet и users/{id}/progress - поток P1 (test_progress_api.py), задания,
# бейджи и колесо - поток P2 (test_rewards_api.py); здесь только проверка доступа.
PUBLIC_REWARD_ROUTES: list[tuple[str, str]] = [
    ("GET", "/v1/me/quests"),
    ("GET", "/v1/me/badges"),
    ("GET", "/v1/wheel"),
    ("POST", "/v1/wheel/spin"),
]
ADMIN_STUBS: list[tuple[str, str, object, str]] = [
    ("GET", "/admin/v1/configs", None, "progression.config.manage"),
    ("GET", "/admin/v1/configs/1", None, "progression.config.manage"),
    ("PUT", "/admin/v1/configs/draft", {"config": {}, **REASON}, "progression.config.manage"),
    (
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 2, **REASON},
        "progression.config.publish",
    ),
    ("POST", "/admin/v1/configs/1/rollback", REASON, "progression.config.publish"),
    ("GET", f"/admin/v1/users/{UID}/ledger", None, "progression.ledger.read"),
    (
        "POST",
        f"/admin/v1/users/{UID}/ledger/grant",
        {"amount": 10, **REASON},
        "progression.ledger.grant",
    ),
    ("POST", "/admin/v1/ledger/op-1/reverse", REASON, "progression.ledger.reverse"),
    ("GET", "/admin/v1/quests", None, "progression.config.manage"),
    ("GET", "/admin/v1/badges", None, "progression.config.manage"),
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


@pytest.mark.parametrize(("method", "path"), PUBLIC_REWARD_ROUTES)
async def test_public_reward_routes_need_account(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer], method: str, path: str
) -> None:
    """Задания, бейджи, колесо реализованы (P2): без токена 401, гостю 403."""
    client, issuer = stub_client
    assert_problem(await client.request(method, path), 401, "auth.unauthenticated")
    guest = {"Authorization": f"Bearer {issuer.issue_guest()}"}
    assert_problem(await client.request(method, path, headers=guest), 403, "auth.guest_forbidden")


@pytest.mark.parametrize(("method", "path", "body", "perm"), ADMIN_STUBS)
async def test_admin_stubs_need_specific_permission_and_answer_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    body: object,
    perm: str,
) -> None:
    client, issuer = stub_client

    async def call(token: str | None) -> httpx.Response:
        headers = dict(KEY)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await client.request(method, path, json=body, headers=headers)

    assert_problem(await call(None), 401, "auth.unauthenticated")
    for token in (
        issuer.issue(),
        issuer.issue(perms=["admin.access"]),
        issuer.issue(perms=[perm]),
    ):
        assert_problem(await call(token), 403, "auth.forbidden")
    allowed = await call(issuer.issue(perms=["admin.access", perm]))
    assert_problem(allowed, 501, "common.not_implemented")
