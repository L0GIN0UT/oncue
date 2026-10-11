"""Сервисные токены (D13, план этапа 2 §2.8): выдача и защита internal-ручек identity."""

from __future__ import annotations

from typing import Any

import jwt
import pytest

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

ADMIN_SECRET = "admin-client-secret-0123456789abcdef"
GAME_SECRET = "game-client-secret-0123456789abcdef"
PASSWORD = "correct horse battery"


async def _issue(service: Any, client_id: str, secret: str, audience: str) -> Any:
    return await service.client.post(
        "/internal/v1/service-tokens",
        json={"client_id": client_id, "client_secret": secret, "audience": audience},
    )


async def test_issues_service_jwt_with_minimal_claims(service: Any) -> None:
    response = await _issue(service, "game", GAME_SECRET, "media_pipeline")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    token = body["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"
    claims = jwt.decode(token, options={"verify_signature": False})
    assert set(claims) == {"iss", "aud", "sub", "scope", "iat", "exp", "jti"}
    assert claims["sub"] == "svc:game"
    assert claims["aud"] == "media_pipeline"
    assert claims["scope"] == "assets.link"
    assert claims["iss"] == "https://identity.test"
    assert claims["exp"] - claims["iat"] == 600
    # подписан ключом из JWKS identity
    jwks = (await service.client.get("/.well-known/jwks.json")).json()
    assert jwt.get_unverified_header(token)["kid"] in {k["kid"] for k in jwks["keys"]}
    # ни секрет, ни токен не попали в логи
    dumped = repr(service.logs)
    assert GAME_SECRET not in dumped
    assert token not in dumped
    assert any(e.get("event") == "auth.service_token_issued" for e in service.logs)


@pytest.mark.parametrize(
    ("client_id", "secret"),
    [("game", "wrong-secret"), ("game", ADMIN_SECRET), ("ghost", GAME_SECRET)],
)
async def test_bad_credentials(service: Any, client_id: str, secret: str) -> None:
    response = await _issue(service, client_id, secret, "media_pipeline")
    assert_problem(response, 401, "auth.client_invalid")


async def test_foreign_audience_is_forbidden(service: Any) -> None:
    assert_problem(
        await _issue(service, "game", GAME_SECRET, "identity"), 403, "auth.audience_forbidden"
    )
    assert_problem(
        await _issue(service, "admin", ADMIN_SECRET, "media_pipeline"),
        403,
        "auth.audience_forbidden",
    )


async def test_request_schema_is_strict(service: Any) -> None:
    body = {"client_id": "game", "client_secret": GAME_SECRET, "audience": "media_pipeline"}
    for bad in ({**body, "extra": 1}, {**body, "audience": "Media"}, {**body, "client_secret": ""}):
        response = await service.client.post("/internal/v1/service-tokens", json=bad)
        assert response.status_code == 422


async def test_wrong_secrets_from_one_ip_do_not_block_other_ip(service: Any) -> None:
    """Регресс DoS: шквал неверных секретов с одного IP не мешает выдаче с другого IP."""
    body = {"client_id": "game", "client_secret": "wrong", "audience": "media_pipeline"}
    async with service.client_from("203.0.113.9") as attacker:
        for _ in range(30):  # лимит неудач по умолчанию — 30 в минуту на (client_id, IP)
            response = await attacker.post("/internal/v1/service-tokens", json=body)
            assert_problem(response, 401, "auth.client_invalid")
        limited = await attacker.post("/internal/v1/service-tokens", json=body)
    problem = assert_problem(limited, 429, "rate.limited")
    assert int(limited.headers["Retry-After"]) == problem["details"]["retry_after"]

    async with service.client_from("10.0.0.5") as honest:
        response = await honest.post(
            "/internal/v1/service-tokens", json={**body, "client_secret": GAME_SECRET}
        )
    assert response.status_code == 200, response.text


async def test_admin_sessions_require_service_token_with_scope(service: Any) -> None:
    await service.register("boss", "boss@example.com")
    body = {"login": "boss", "password": PASSWORD}
    url = "/internal/v1/admin-sessions"

    assert_problem(await service.client.post(url, json=body), 401, "auth.unauthenticated")

    # пользовательский токен (aud = guessverse) internal не проходит
    login = await service.login("boss")
    user_token = login.json().get("access_token") or service.client.cookies.get("gv_access")
    assert user_token
    service.client.cookies.clear()
    user = {"Authorization": f"Bearer {user_token}"}
    assert_problem(
        await service.client.post(url, json=body, headers=user), 401, "auth.token_invalid"
    )

    # сервисный токен другому адресату
    game_media = (await _issue(service, "game", GAME_SECRET, "media_pipeline")).json()
    foreign = {"Authorization": f"Bearer {game_media['access_token']}"}
    assert_problem(
        await service.client.post(url, json=body, headers=foreign), 401, "auth.token_invalid"
    )

    ok = await service.client.post(url, json=body, headers=service.admin_client)
    assert ok.status_code == 200, ok.text
    for path in ("/refresh", "/revoke"):
        assert_problem(
            await service.client.post(url + path, json={"refresh_token": "x"}),
            401,
            "auth.unauthenticated",
        )


async def test_service_token_does_not_pass_public_api(service: Any) -> None:
    for client_id, secret, audience in (
        ("admin", ADMIN_SECRET, "identity"),
        ("game", GAME_SECRET, "media_pipeline"),
    ):
        token = (await _issue(service, client_id, secret, audience)).json()["access_token"]
        response = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert_problem(response, 401, "auth.token_invalid")
