"""Заглушки 0a этапа 4b: API rating — авторизация и 501 (поток G заменит их)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from rating_service.main import DEFINITION
from rating_service.settings import Settings

from gv_core.ids import uuid7
from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

IDEMPOTENCY = {"Idempotency-Key": "idem-00000001"}
UID, MID, SID = str(uuid7()), str(uuid7()), str(uuid7())
REASON = {"reason": "проверка контракта"}

# (метод, путь, тело, нужен ли Idempotency-Key)
Case = tuple[str, str, Any, bool]
PUBLIC_OPEN: list[Case] = [
    ("GET", "/v1/ladders", None, False),
    ("GET", "/v1/ladders/film/leaderboard", None, False),
    ("GET", "/v1/ladders/film/weekly", None, False),
]
PUBLIC_USER: list[Case] = [
    ("GET", "/v1/me/ratings", None, False),
    ("GET", f"/v1/users/{UID}/ratings", None, False),
    ("GET", "/v1/me/ratings/film/history", None, False),
    ("GET", f"/v1/matches/{MID}/changes", None, False),
]
ADMIN_LADDERS: list[Case] = [
    ("GET", "/admin/v1/ladders", None, False),
    ("GET", "/admin/v1/ladders/film/versions", None, False),
    ("GET", "/admin/v1/ladders/film/versions/1", None, False),
    ("PUT", "/admin/v1/ladders/film/draft", {"config": {"key": "film"}, **REASON}, True),
    ("POST", "/admin/v1/ladders/film/publish", {"version": 2, **REASON}, True),
    ("POST", "/admin/v1/ladders/film/rollback", {"to_version": 1, **REASON}, True),
]
ADMIN_SEASONS: list[Case] = [
    ("POST", "/admin/v1/ladders/film/seasons", REASON, True),
    ("POST", f"/admin/v1/seasons/{SID}/close", REASON, True),
    ("POST", "/admin/v1/ladders/film/leaderboard/rebuild", REASON, True),
]


def _ids(cases: list[Case]) -> list[str]:
    return [f"{m} {p}" for m, p, _, _ in cases]


@pytest.fixture
async def client(token_issuer: TestTokenIssuer) -> AsyncIterator[httpx.AsyncClient]:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    app = create_app(DEFINITION, infra, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as http:
            yield http
    finally:
        await infra.close()


async def _call(client: httpx.AsyncClient, case: Case, token: str | None) -> httpx.Response:
    method, path, body, idempotent = case
    headers = dict(IDEMPOTENCY) if idempotent else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("case", PUBLIC_OPEN, ids=_ids(PUBLIC_OPEN))
async def test_public_reads_are_open_and_501(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer, case: Case
) -> None:
    assert_problem(await _call(client, case, None), 501, "common.not_implemented")
    guest = await _call(client, case, token_issuer.issue_guest())
    assert_problem(guest, 501, "common.not_implemented")


@pytest.mark.parametrize("case", PUBLIC_USER, ids=_ids(PUBLIC_USER))
async def test_personal_reads_need_user(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer, case: Case
) -> None:
    assert_problem(await _call(client, case, None), 401, "auth.unauthenticated")
    guest = await _call(client, case, token_issuer.issue_guest())
    assert_problem(guest, 403, "auth.guest_forbidden")
    assert_problem(await _call(client, case, token_issuer.issue()), 501, "common.not_implemented")


async def test_around_accepts_only_me(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/ladders/film/leaderboard", params={"around": "all"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("cases", "perm"),
    [(ADMIN_LADDERS, "rating.ladders.publish"), (ADMIN_SEASONS, "rating.seasons.manage")],
    ids=["ladders", "seasons"],
)
async def test_admin_needs_specific_permission(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer, cases: list[Case], perm: str
) -> None:
    for case in cases:
        assert_problem(await _call(client, case, None), 401, "auth.unauthenticated")
        for token in (
            token_issuer.issue_guest(),
            token_issuer.issue(perms=["admin.access"]),
            token_issuer.issue(perms=[perm]),
        ):
            assert_problem(await _call(client, case, token), 403, "auth.forbidden")
        allowed = token_issuer.issue(perms=["admin.access", perm])
        assert_problem(await _call(client, case, allowed), 501, "common.not_implemented")
