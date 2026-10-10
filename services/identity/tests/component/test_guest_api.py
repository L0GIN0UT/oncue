"""Гостевые сессии через HTTP (D5 плана этапа 2): выдача, refresh, лимиты, границы прав."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from prometheus_client import REGISTRY

from gv_auth import ACCESS_COOKIE
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

ORIGIN = "https://gv.test"
GUEST_REFRESH_TTL = 2_592_000


def _set_cookies(response: Any) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def _claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, options={"verify_signature": False})


async def _start(service: Any, **headers: str) -> Any:
    return await service.client.post("/v1/auth/guest", headers={"Origin": ORIGIN, **headers})


def _started_total() -> float:
    return REGISTRY.get_sample_value("identity_guest_sessions_started_total") or 0.0


async def test_guest_session_issued_with_cookies_and_rightless_token(service: Any) -> None:
    before = _started_total()

    response = await _start(service)

    assert response.status_code == 204, response.text
    assert response.headers["cache-control"] == "no-store"
    cookies = _set_cookies(response)
    for name in ("gv_access", "gv_refresh"):
        assert "HttpOnly" in cookies[name]
        assert "samesite=lax" in cookies[name].lower()
    assert "Path=/v1/auth" in cookies["gv_refresh"]
    assert f"Max-Age={GUEST_REFRESH_TTL}" in cookies["gv_refresh"]
    assert "HttpOnly" not in cookies["gv_csrf"]

    claims = _claims(service.client.cookies.get(ACCESS_COOKIE))
    assert uuid.UUID(claims["sub"]).version == 7
    assert claims["roles"] == ["guest"]
    assert claims["perms"] == []
    assert claims["gst"] is True
    assert claims["sid"] and claims["sid"] != claims["sub"]
    assert "plan" not in claims
    assert _started_total() == before + 1
    assert not await service.events()  # гость не порождает событий identity


async def test_guest_requires_same_origin(service: Any) -> None:
    for headers in (
        {"Origin": "https://evil.test"},
        {"Origin": "null"},
        {"Referer": "https://evil.test/page"},
    ):
        response = await service.client.post("/v1/auth/guest", headers=headers)
        assert response.status_code == 403, headers
    assert ACCESS_COOKIE not in service.client.cookies
    # Без Origin и Referer — не браузер (как у входа, require_same_origin): лимит по IP.
    assert (await service.client.post("/v1/auth/guest")).status_code == 204


async def test_guest_conflicts_with_live_session(service: Any) -> None:
    assert (await _start(service)).status_code == 204
    # Обе cookie на месте (без CSRF-заголовка: ручка только отказывает).
    assert_problem(await _start(service), 409, "auth.already_authenticated")
    # Access-cookie истекла, refresh жив — сессия всё ещё есть.
    service.client.cookies.delete(ACCESS_COOKIE)
    assert_problem(await _start(service), 409, "auth.already_authenticated")


async def test_guest_conflicts_with_user_bearer(service: Any) -> None:
    await service.register()
    await service.login()
    access = service.client.cookies.get(ACCESS_COOKIE)
    service.client.cookies.clear()
    response = await _start(service, Authorization=f"Bearer {access}")
    assert_problem(response, 409, "auth.already_authenticated")


async def test_guest_new_session_after_logout(service: Any) -> None:
    assert (await _start(service)).status_code == 204
    first_sid = _claims(service.client.cookies.get(ACCESS_COOKIE))["sid"]
    logout = await service.client.post("/v1/auth/logout", headers=service.csrf())
    assert logout.status_code == 204
    assert (await _start(service)).status_code == 204
    assert _claims(service.client.cookies.get(ACCESS_COOKIE))["sid"] != first_sid


async def test_guest_rate_limited_per_ip(service: Any) -> None:
    limit = service.infra.settings.guest.per_ip_per_hour
    for _ in range(limit):
        service.client.cookies.clear()
        assert (await _start(service)).status_code == 204
    service.client.cookies.clear()
    body = assert_problem(await _start(service), 429, "rate.limited")
    assert body["details"]["retry_after"] >= 1


async def test_guest_refresh_rotates_and_reuse_revokes(service: Any) -> None:
    await _start(service)
    stolen = service.client.cookies.get("gv_refresh")

    refreshed = await service.client.post("/v1/auth/refresh", headers=service.csrf())

    assert refreshed.status_code == 200, refreshed.text
    user = refreshed.json()["user"]
    assert (user["kind"], user["roles"], user["perms"]) == ("guest", ["guest"], [])
    access = service.client.cookies.get(ACCESS_COOKIE)
    assert _claims(access)["gst"] is True
    assert f"Max-Age={GUEST_REFRESH_TTL}" in _set_cookies(refreshed)["gv_refresh"]

    service.client.cookies.set("gv_refresh", stolen)
    reused = await service.client.post("/v1/auth/refresh", headers=service.csrf())
    assert_problem(reused, 401, "auth.refresh_reused")
    # Сессия отозвана целиком: свежий access-токен тоже больше не принимается.
    me = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {access}"})
    assert_problem(me, 401, "auth.session_revoked")


async def test_guest_token_does_not_pass_account_admin_and_internal(service: Any) -> None:
    await _start(service)
    bearer = {"Authorization": f"Bearer {service.client.cookies.get(ACCESS_COOKIE)}"}
    refresh = service.client.cookies.get("gv_refresh")

    for method, path in (
        ("GET", "/v1/me"),
        ("GET", "/v1/me/linked-accounts"),
        ("POST", "/v1/auth/logout-all"),
        ("POST", "/v1/me/linked-accounts/yandex"),
    ):
        response = await service.client.request(method, path, headers=bearer)
        assert_problem(response, 403, "auth.guest_forbidden")
    for path in ("/admin/v1/users", "/admin/v1/roles"):
        assert_problem(await service.client.get(path, headers=bearer), 403, "auth.forbidden")
    admin_refresh = await service.client.post(
        "/internal/v1/admin-sessions/refresh", json={"refresh_token": refresh}
    )
    assert_problem(admin_refresh, 401, "auth.refresh_invalid")
    # Гостевая сессия при этом жива.
    assert (
        await service.client.post("/v1/auth/refresh", headers=service.csrf())
    ).status_code == 200


async def test_login_from_guest_session_revokes_it(service: Any) -> None:
    await service.register()
    await _start(service)
    guest_access = service.client.cookies.get(ACCESS_COOKIE)
    guest_bearer = {"Authorization": f"Bearer {guest_access}"}
    assert_problem(
        await service.client.get("/v1/me", headers=guest_bearer), 403, "auth.guest_forbidden"
    )

    login = await service.login()

    assert login.status_code == 200, login.text
    assert login.json()["user"]["kind"] == "user"
    assert_problem(
        await service.client.get("/v1/me", headers=guest_bearer), 401, "auth.session_revoked"
    )
    assert (await service.client.get("/v1/me")).status_code == 200  # cookie уже пользователя
    assert "gst" not in _claims(service.client.cookies.get(ACCESS_COOKIE))


async def test_failed_login_keeps_guest_session(service: Any) -> None:
    await service.register()
    await _start(service)
    assert (await service.login(password="wrong password!")).status_code == 401
    assert (
        await service.client.post("/v1/auth/refresh", headers=service.csrf())
    ).status_code == 200
