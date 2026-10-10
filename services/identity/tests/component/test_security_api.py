"""Регрессии security-ревью через HTTP: лимиты admin-входа, повторная аутентификация,
идемпотентность admin-мутаций, отзыв сессий при понижении прав, denylist TTL."""

from __future__ import annotations

from typing import Any

import pytest
from redis.asyncio import Redis

from gv_auth import ACCESS_COOKIE, revoked_sid_key
from gv_testkit import InfraProvider, assert_problem, assert_published

pytestmark = pytest.mark.component

PASSWORD = "correct horse battery"


async def _admin_session(service: Any, **extra: Any) -> dict[str, Any]:
    await service.register("boss", "boss@example.com")  # bootstrap admin
    response = await service.client.post(
        "/internal/v1/admin-sessions",
        headers=service.admin_client,
        json={"login": "boss@example.com", "password": PASSWORD, **extra},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------------ admin-сессии


async def test_admin_session_returns_identity_version_of_roles_event(service: Any) -> None:
    body = await _admin_session(service)
    changed = assert_published(
        await service.events(), "identity.UserRolesChanged", {"user_id": body["user"]["id"]}
    )
    assert body["identity_version"] == changed.payload["version"]
    refreshed = await service.client.post(
        "/internal/v1/admin-sessions/refresh",
        headers=service.admin_client,
        json={"refresh_token": body["refresh_token"]},
    )
    assert refreshed.json()["identity_version"] == body["identity_version"]


async def test_admin_session_limited_by_client_ip(service: Any) -> None:
    await service.register("boss", "boss@example.com")
    for n in range(10):
        bad = await service.client.post(
            "/internal/v1/admin-sessions",
            headers=service.admin_client,
            json={"login": f"ghost_{n}", "password": "nope nope", "client_ip": "203.0.113.9"},
        )
        assert_problem(bad, 401, "auth.invalid_credentials")
    limited = await service.client.post(
        "/internal/v1/admin-sessions",
        headers=service.admin_client,
        json={"login": "boss", "password": PASSWORD, "client_ip": "203.0.113.9"},
    )
    problem = assert_problem(limited, 429, "rate.limited")
    assert int(limited.headers["Retry-After"]) == problem["details"]["retry_after"]
    other = await service.client.post(
        "/internal/v1/admin-sessions",
        headers=service.admin_client,
        json={"login": "boss", "password": PASSWORD, "client_ip": "198.51.100.1"},
    )
    assert other.status_code == 200, other.text


async def test_admin_session_revoke(service: Any) -> None:
    body = await _admin_session(service)
    for _ in range(2):  # идемпотентно
        revoked = await service.client.post(
            "/internal/v1/admin-sessions/revoke",
            headers=service.admin_client,
            json={"refresh_token": body["refresh_token"]},
        )
        assert revoked.status_code == 204
    unknown = await service.client.post(
        "/internal/v1/admin-sessions/revoke",
        headers=service.admin_client,
        json={"refresh_token": "unknown"},
    )
    assert unknown.status_code == 204
    refresh = await service.client.post(
        "/internal/v1/admin-sessions/refresh",
        headers=service.admin_client,
        json={"refresh_token": body["refresh_token"]},
    )
    assert refresh.status_code == 401
    users = await service.client.get(
        "/admin/v1/users", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert_problem(users, 401, "auth.session_revoked")


async def test_denylist_outlives_access_token_and_leeway(
    service: Any, infra: InfraProvider
) -> None:
    await service.register()
    await service.login()
    me = (await service.client.get("/v1/me")).json()
    assert me["id"]
    await service.client.post("/v1/auth/logout-all", headers=service.csrf())
    redis = Redis.from_url(infra.redis_url())
    try:
        keys = [k async for k in redis.scan_iter(match=f"{service.prefix}auth:revoked_sid:*")]
        assert keys
        ttl = await redis.ttl(keys[0])
    finally:
        await redis.aclose()
    settings = service.infra.settings
    assert settings.jwt.access_ttl_s < ttl <= settings.jwt.access_ttl_s + settings.jwt.leeway_s
    assert revoked_sid_key("x", prefix=service.prefix).startswith(service.prefix)


# ------------------------------------------------------------------------ admin-мутации


async def test_admin_mutation_requires_and_stores_idempotency_key(service: Any) -> None:
    auth = {"Authorization": f"Bearer {(await _admin_session(service))['access_token']}"}
    await service.register()
    (user,) = (await service.client.get("/admin/v1/users?q=neo", headers=auth)).json()["items"]
    url = f"/admin/v1/users/{user['id']}/roles"
    body = {"roles": ["moderator"], "reason": "помогает с каталогом"}

    assert_problem(
        await service.client.put(url, json=body, headers=auth), 422, "idempotency.key_required"
    )
    short_reason = await service.client.put(
        url, json={**body, "reason": "x"}, headers={**auth, "Idempotency-Key": "key-reason-1"}
    )
    assert short_reason.status_code == 422

    headers = {**auth, "Idempotency-Key": "key-assign-77"}
    first = await service.client.put(url, json=body, headers=headers)
    assert first.status_code == 200, first.text
    again = await service.client.put(url, json=body, headers=headers)
    assert again.status_code == 200
    assert again.json() == first.json()
    events = [
        e
        for e in await service.events()
        if e.event_type == "identity.UserRolesChanged" and e.payload["user_id"] == user["id"]
    ]
    assert len(events) == 1
    reused = await service.client.put(
        url, json={"roles": [], "reason": "другое тело"}, headers=headers
    )
    assert_problem(reused, 409, "idempotency.key_reused")

    matrix = await service.client.put(
        "/admin/v1/roles",
        json={"roles": {"user": [], "admin": ["admin.access"]}, "reason": "без ключа"},
        headers=auth,
    )
    assert_problem(matrix, 422, "idempotency.key_required")


async def test_demotion_revokes_sessions_and_access_tokens(service: Any) -> None:
    auth = {"Authorization": f"Bearer {(await _admin_session(service))['access_token']}"}
    await service.register()
    (user,) = (await service.client.get("/admin/v1/users?q=neo", headers=auth)).json()["items"]
    url = f"/admin/v1/users/{user['id']}/roles"
    up = await service.client.put(
        url,
        json={"roles": ["moderator"], "reason": "помощь"},
        headers={**auth, "Idempotency-Key": "key-up-0001"},
    )
    assert up.status_code == 200
    mod = await service.client.post(
        "/internal/v1/admin-sessions",
        headers=service.admin_client,
        json={"login": "neo_2026", "password": PASSWORD},
    )
    mod_auth = {"Authorization": f"Bearer {mod.json()['access_token']}"}
    assert (await service.client.get("/admin/v1/users", headers=mod_auth)).status_code == 200

    down = await service.client.put(
        url,
        json={"roles": [], "reason": "снят с модерации"},
        headers={**auth, "Idempotency-Key": "key-down-001"},
    )
    assert down.status_code == 200
    assert_problem(
        await service.client.get("/admin/v1/users", headers=mod_auth),
        401,
        "auth.session_revoked",
    )
    refresh = await service.client.post(
        "/internal/v1/admin-sessions/refresh",
        headers=service.admin_client,
        json={"refresh_token": mod.json()["refresh_token"]},
    )
    assert refresh.status_code == 401


# ------------------------------------------------------------------------ повторная аутентификация


async def test_link_and_unlink_require_password(service: Any) -> None:
    await service.register()
    await service.login()
    no_password = await service.client.post(
        "/v1/me/linked-accounts/yandex", json={"return_to": "/profile"}, headers=service.csrf()
    )
    assert_problem(no_password, 422, "auth.password_required")
    wrong = await service.client.post(
        "/v1/me/linked-accounts/yandex",
        json={"return_to": "/profile", "password": "not my password"},
        headers=service.csrf(),
    )
    assert_problem(wrong, 401, "auth.invalid_credentials")
    unlink = await service.client.request(
        "DELETE", "/v1/me/linked-accounts/yandex", headers=service.csrf()
    )
    assert_problem(unlink, 422, "auth.password_required")


async def test_delete_me_password_check_is_rate_limited(service: Any) -> None:
    await service.register()
    await service.login()
    for _ in range(5):
        wrong = await service.client.request(
            "DELETE", "/v1/me", json={"password": "nope nope nope"}, headers=service.csrf()
        )
        assert_problem(wrong, 401, "auth.invalid_credentials")
    limited = await service.client.request(
        "DELETE", "/v1/me", json={"password": PASSWORD}, headers=service.csrf()
    )
    assert_problem(limited, 429, "rate.limited")
    assert service.client.cookies.get(ACCESS_COOKIE)
    assert (await service.client.get("/v1/me")).status_code == 200
