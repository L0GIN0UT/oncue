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


# ------------------------------------------------------------------ доступ к ручкам наград (P2)
# me/progress, me/wallet и users/{id}/progress - поток P1 (test_progress_api.py), задания,
# бейджи и колесо - поток P2 (test_rewards_api.py); здесь только проверка доступа.
PUBLIC_REWARD_ROUTES: list[tuple[str, str]] = [
    ("GET", "/v1/me/quests"),
    ("GET", "/v1/me/badges"),
    ("GET", "/v1/wheel"),
    ("POST", "/v1/wheel/spin"),
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
