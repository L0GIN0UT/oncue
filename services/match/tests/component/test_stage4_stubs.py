"""Контракт шага 0a этапа 4 (§2.5): ручки match объявлены, авторизация до заглушки 501.

Каждая публичная ручка: без токена — 401, гость — 403 ``auth.guest_forbidden`` (D13),
пользователь — 501. Admin: нужны ``admin.access`` + ``match.manage``. Неверный код
комнаты — 422 (D12).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from match_service.main import DEFINITION
from match_service.settings import Settings

from gv_auth import ACCESS_COOKIE
from gv_core.ids import uuid7
from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

CODE = "K7MQ2ZXA"
OP = {"op_id": "op-00000001"}
IDEMPOTENCY = {"Idempotency-Key": "idem-00000001"}
MATCH_ID = str(uuid7())
USER_ID = str(uuid7())
GUESS = {**OP, "expected_version": 3, "round_index": 0, "level": 1, "answer": {"text": "Брат"}}

# (метод, путь, тело, нужен ли Idempotency-Key)
PUBLIC: list[tuple[str, str, dict[str, Any] | None, bool]] = [
    ("POST", "/v1/rooms", {"mode_key": "film.lobby.collection"}, True),
    ("GET", "/v1/rooms/open", None, False),
    ("GET", f"/v1/rooms/{CODE}", None, False),
    ("POST", f"/v1/rooms/{CODE}/join", OP, False),
    ("POST", f"/v1/rooms/{CODE}/leave", OP, False),
    ("POST", f"/v1/rooms/{CODE}/ready", {**OP, "ready": True}, False),
    ("POST", f"/v1/rooms/{CODE}/settings", {**OP, "settings": {"rounds": 12}}, False),
    ("POST", f"/v1/rooms/{CODE}/kick", {**OP, "user_id": USER_ID}, False),
    ("POST", f"/v1/rooms/{CODE}/transfer-host", {**OP, "user_id": USER_ID}, False),
    ("POST", f"/v1/rooms/{CODE}/reset-memory", OP, False),
    ("POST", f"/v1/rooms/{CODE}/start", {**OP, "expected_version": 5}, False),
    ("POST", f"/v1/rooms/{CODE}/rematch", OP, False),
    ("POST", f"/v1/rooms/{CODE}/realtime-token", None, False),
    ("GET", f"/v1/rooms/{CODE}/state", None, False),
    ("POST", f"/v1/rooms/{CODE}/tick", {"known_version": 4}, False),
    ("POST", f"/v1/rooms/{CODE}/match/guess", GUESS, False),
    (
        "POST",
        f"/v1/rooms/{CODE}/match/skip",
        {**OP, "expected_version": 3, "round_index": 0, "level": 1},
        False,
    ),
    ("POST", f"/v1/rooms/{CODE}/match/forfeit", OP, False),
    ("POST", f"/v1/rooms/{CODE}/match/emote", {**OP, "kind": "emote", "key": "emote.gg"}, False),
    ("POST", f"/v1/rooms/{CODE}/match/chat", {**OP, "text": "удачи всем"}, False),
    ("POST", f"/v1/rooms/{CODE}/match/away", {**OP, "away": True}, False),
    ("POST", f"/v1/matches/{MATCH_ID}/rate", {"to_user_id": USER_ID, "kind": "praise"}, True),
    ("GET", f"/v1/matches/{MATCH_ID}", None, False),
    ("GET", "/v1/me/matches", None, False),
]
ADMIN: list[tuple[str, str, dict[str, Any] | None, bool]] = [
    ("GET", "/admin/v1/rooms", None, False),
    ("POST", f"/admin/v1/rooms/{CODE}/abort", {"reason": "зависшая комната"}, True),
    ("GET", f"/admin/v1/matches/{MATCH_ID}", None, False),
]
ROOM_PATHS = [p for _, p, _, _ in PUBLIC if p.startswith(f"/v1/rooms/{CODE}")]


def _ids(cases: list[tuple[str, str, Any, bool]]) -> list[str]:
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


async def _call(
    client: httpx.AsyncClient,
    case: tuple[str, str, dict[str, Any] | None, bool],
    token: str | None,
    *,
    path: str | None = None,
) -> httpx.Response:
    method, case_path, body, idempotent = case
    headers = dict(IDEMPOTENCY) if idempotent else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.request(method, path or case_path, json=body, headers=headers)


@pytest.mark.parametrize("case", PUBLIC, ids=_ids(PUBLIC))
async def test_public_requires_user_then_stub(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer, case: Any
) -> None:
    assert_problem(await _call(client, case, None), 401, "auth.unauthenticated")
    guest = await _call(client, case, token_issuer.issue_guest())
    assert_problem(guest, 403, "auth.guest_forbidden")

    response = await _call(client, case, token_issuer.issue())

    assert_problem(response, 501, "common.not_implemented")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("case", ADMIN, ids=_ids(ADMIN))
async def test_admin_requires_match_manage_then_stub(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer, case: Any
) -> None:
    assert_problem(await _call(client, case, None), 401, "auth.unauthenticated")
    for token in (
        token_issuer.issue_guest(),
        token_issuer.issue(perms=["admin.access"]),
        token_issuer.issue(perms=["match.manage"]),
    ):
        assert_problem(await _call(client, case, token), 403, "auth.forbidden")

    admin = token_issuer.issue(perms=["admin.access", "match.manage"])

    assert_problem(await _call(client, case, admin), 501, "common.not_implemented")


@pytest.mark.parametrize(
    "bad", ["k7mq2zxa", "K7MQ2ZX", "K7MQ2ZXAB", "K7MQ2ZX0", "K7MQ2ZXO", "K7MQ2ZX1", "K7MQ2ZXI"]
)
async def test_invalid_room_code_is_422(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer, bad: str
) -> None:
    user = token_issuer.issue()
    admin = token_issuer.issue(perms=["admin.access", "match.manage"])
    for case in PUBLIC:
        if case[1] in ROOM_PATHS:
            path = case[1].replace(CODE, bad)
            response = await _call(client, case, user, path=path)
            assert response.status_code == 422, (path, response.text)
    abort = ADMIN[1]
    response = await _call(client, abort, admin, path=abort[1].replace(CODE, bad))
    assert response.status_code == 422, response.text


async def test_room_commands_require_op_id_and_phase_commands_expected_version(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    bearer = {"Authorization": f"Bearer {token_issuer.issue()}"}
    no_op = await client.post(f"/v1/rooms/{CODE}/join", json={}, headers=bearer)
    assert no_op.status_code == 422
    no_version = await client.post(f"/v1/rooms/{CODE}/start", json=OP, headers=bearer)
    assert no_version.status_code == 422
    extra = await client.post(
        f"/v1/rooms/{CODE}/ready", json={**OP, "ready": True, "x": 1}, headers=bearer
    )
    assert extra.status_code == 422


async def test_create_room_requires_idempotency_key(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    bearer = {"Authorization": f"Bearer {token_issuer.issue()}"}
    response = await client.post(
        "/v1/rooms", json={"mode_key": "film.lobby.collection"}, headers=bearer
    )
    assert_problem(response, 422, "idempotency.key_required")


async def test_cookie_mutation_without_csrf_is_rejected(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    client.cookies.set(ACCESS_COOKIE, token_issuer.issue())
    try:
        response = await client.post(
            f"/v1/rooms/{CODE}/join", json=OP, headers={"Origin": "https://evil.test"}
        )
    finally:
        client.cookies.clear()
    assert_problem(response, 403, "auth.csrf_failed")
