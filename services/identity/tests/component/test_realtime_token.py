"""``POST /v1/realtime/token`` (I1 плана этапа 4, D9, ADR 0035): доступ, claims, лимит, изоляция."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import jwt
import pytest

from gv_auth import ACCESS_COOKIE, CSRF_HEADER, JwtVerifier
from gv_core import UnauthenticatedError
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

URL = "/v1/realtime/token"
TTL_S = 600  # GV_REALTIME__TOKEN_TTL_S по умолчанию
LIMIT = 5  # realtime_token_limit.per_user в настройках теста
# Те же значения, что в conftest (тестовые модули не импортируют conftest).
ORIGIN = "https://gv.test"
ADMIN_SECRET = "admin-client-secret-0123456789abcdef"
REALTIME_SECRET = "realtime-connection-secret-0123456789abcdef"


async def _logged_in(service: Any) -> None:
    await service.register()
    assert (await service.login()).status_code == 200


async def _issue(service: Any, **headers: str) -> Any:
    return await service.client.post(URL, headers=headers or service.csrf())


def _decode(token: str, audience: str = "centrifugo") -> dict[str, Any]:
    return jwt.decode(
        token,
        REALTIME_SECRET,
        algorithms=["HS256"],
        audience=audience,
        options={"require": ["sub", "aud", "iat", "exp"]},
    )


async def test_issues_minimal_connection_token(service: Any) -> None:
    await _logged_in(service)
    access = jwt.decode(
        service.client.cookies.get(ACCESS_COOKIE), options={"verify_signature": False}
    )

    response = await _issue(service)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"token", "expires_at"}
    token = body["token"]
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    claims = _decode(token)
    # Только sub/aud/iat/exp: без channels/subs/info/прав/sid.
    assert set(claims) == {"sub", "aud", "iat", "exp"}
    assert claims["sub"] == access["sub"]
    assert claims["exp"] - claims["iat"] == TTL_S
    assert datetime.fromisoformat(body["expires_at"]).timestamp() == claims["exp"]
    # Токен не попал в логи.
    assert token not in repr(service.logs)
    assert any(e.get("event") == "realtime.token_issued" for e in service.logs)


async def test_bearer_access_token_is_enough(service: Any) -> None:
    """Bearer (не cookie) — CSRF не нужен; Origin, если есть, проверяется."""
    await _logged_in(service)
    access = service.client.cookies.get(ACCESS_COOKIE)
    service.client.cookies.clear()
    response = await service.client.post(URL, headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 200, response.text


async def test_without_session_401(service: Any) -> None:
    assert_problem(
        await service.client.post(URL, headers={"Origin": ORIGIN}), 401, "auth.unauthenticated"
    )


async def test_guest_403(service: Any) -> None:
    started = await service.client.post("/v1/auth/guest", headers={"Origin": ORIGIN})
    assert started.status_code == 204, started.text
    assert_problem(await _issue(service), 403, "auth.guest_forbidden")
    bearer = {"Authorization": f"Bearer {service.client.cookies.get(ACCESS_COOKIE)}"}
    service.client.cookies.clear()
    assert_problem(await service.client.post(URL, headers=bearer), 403, "auth.guest_forbidden")


async def test_revoked_sid_401(service: Any) -> None:
    await _logged_in(service)
    access = service.client.cookies.get(ACCESS_COOKIE)
    assert (await service.client.post("/v1/auth/logout", headers=service.csrf())).status_code == 204
    response = await service.client.post(URL, headers={"Authorization": f"Bearer {access}"})
    assert_problem(response, 401, "auth.session_revoked")


async def test_foreign_origin_403(service: Any) -> None:
    await _logged_in(service)
    csrf = service.csrf()[CSRF_HEADER]
    # Cookie-запрос с чужого сайта.
    cookie_req = await _issue(service, Origin="https://evil.test", **{CSRF_HEADER: csrf})
    assert_problem(cookie_req, 403, "auth.csrf_failed")
    # Cookie-запрос без CSRF-токена.
    assert_problem(await _issue(service, Origin=ORIGIN), 403, "auth.csrf_failed")
    # Bearer с чужим Origin — require_same_origin.
    access = service.client.cookies.get(ACCESS_COOKIE)
    service.client.cookies.clear()
    bearer_req = await service.client.post(
        URL, headers={"Authorization": f"Bearer {access}", "Origin": "https://evil.test"}
    )
    assert_problem(bearer_req, 403, "auth.csrf_failed")


async def test_rate_limited_per_user(service: Any) -> None:
    await _logged_in(service)
    for _ in range(LIMIT):
        assert (await _issue(service)).status_code == 200
    limited = await _issue(service)
    assert_problem(limited, 429, "rate.limited")


async def test_service_token_rejected_401(service: Any) -> None:
    issued = await service.client.post(
        "/internal/v1/service-tokens",
        json={"client_id": "admin", "client_secret": ADMIN_SECRET, "audience": "identity"},
    )
    headers = {"Authorization": f"Bearer {issued.json()['access_token']}"}
    assert_problem(await service.client.post(URL, headers=headers), 401, "auth.token_invalid")


async def test_realtime_token_is_not_an_access_token(service: Any) -> None:
    """Другой ключ (HS256 vs JWKS) и другой aud: ни один верификатор gv_auth его не примет."""
    await _logged_in(service)
    token = (await _issue(service)).json()["token"]

    async def load_jwks() -> Mapping[str, Any]:
        jwks: Mapping[str, Any] = (await service.client.get("/.well-known/jwks.json")).json()
        return jwks

    issuer = service.infra.settings.jwt_issuer
    # Верификатор сервиса-потребителя (aud = guessverse) и даже с aud = centrifugo:
    # HS* запрещены, алгоритм отвергается раньше aud.
    for audience in ("guessverse", "identity", "centrifugo"):
        verifier = JwtVerifier(load_jwks, issuer=issuer, audience=audience)
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)
        with pytest.raises(UnauthenticatedError):
            await verifier.decode(token)  # путь ServiceAuthenticator (сервисные токены)

    # И по HTTP: публичная ручка с access-токеном и internal-ручка с сервисным.
    service.client.cookies.clear()
    bearer = {"Authorization": f"Bearer {token}"}
    assert_problem(await service.client.get("/v1/me", headers=bearer), 401, "auth.token_invalid")
    internal = await service.client.post(
        "/internal/v1/admin-sessions/revoke", json={"refresh_token": "x" * 43}, headers=bearer
    )
    assert_problem(internal, 401, "auth.token_invalid")
