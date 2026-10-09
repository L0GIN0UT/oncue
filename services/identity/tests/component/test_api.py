"""identity через HTTP: вход, сессии, CSRF, привязка Яндекса, admin API, события."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from gv_auth import ACCESS_COOKIE, CSRF_COOKIE
from gv_testkit import assert_event_matches_schema, assert_problem, assert_published

pytestmark = pytest.mark.component

ORIGIN = "https://gv.test"
PASSWORD = "correct horse battery"
YA_TOKEN = "https://ya.test/token"
YA_INFO = "https://ya.test/info"


def _set_cookies(response: Any) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


async def test_health(service: Any) -> None:
    assert (await service.client.get("/health/live")).status_code == 200
    assert (await service.client.get("/health/ready")).status_code == 200
    meta = await service.client.get("/v1/meta")
    assert meta.json() == {"service": "identity", "status": "active"}


# ----------------------------------------------------------------------------- вход


async def test_register_verify_login_refresh(service: Any) -> None:
    await service.register()
    response = await service.login("neo@example.com")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["roles"] == ["user"]

    cookies = _set_cookies(response)
    access, refresh, csrf = cookies["gv_access"], cookies["gv_refresh"], cookies["gv_csrf"]
    for header in (access, refresh):
        assert "HttpOnly" in header
        assert "samesite=lax" in header.lower()
    assert "Path=/v1/auth" in refresh  # refresh уходит только на ручки auth
    assert "Path=/;" in access or access.endswith("Path=/")
    assert "HttpOnly" not in csrf  # фронтенд читает CSRF-токен

    me = await service.client.get("/v1/me")
    assert me.status_code == 200
    assert me.json()["nickname"] == "neo_2026"
    assert me.json()["email_verified"] is True

    old_csrf = service.client.cookies.get(CSRF_COOKIE)
    refreshed = await service.client.post("/v1/auth/refresh", headers=service.csrf())
    assert refreshed.status_code == 200, refreshed.text
    assert service.client.cookies.get(CSRF_COOKIE) != old_csrf  # CSRF перевыдаётся


async def test_login_by_nickname_and_same_error_for_unknown(service: Any) -> None:
    await service.register()
    assert (await service.login("NEO_2026")).status_code == 200
    unknown = assert_problem(await service.login("ghost"), 401, "auth.invalid_credentials")
    wrong = assert_problem(
        await service.login("neo_2026", "wrong password!"), 401, "auth.invalid_credentials"
    )
    assert unknown["title"] == wrong["title"]


async def test_unverified_cannot_login(service: Any) -> None:
    await service.register(verify=False)
    assert_problem(await service.login(), 403, "auth.email_not_verified")


async def test_refresh_reuse_revokes_session(service: Any) -> None:
    await service.register()
    await service.login()
    stolen = service.client.cookies.get("gv_refresh")
    assert (
        await service.client.post("/v1/auth/refresh", headers=service.csrf())
    ).status_code == 200
    access_after_rotation = service.client.cookies.get(ACCESS_COOKIE)

    service.client.cookies.set("gv_refresh", stolen, path="/v1/auth")
    reused = await service.client.post("/v1/auth/refresh", headers=service.csrf())
    assert_problem(reused, 401, "auth.refresh_reused")
    # Сессия отозвана целиком: даже свежий access-токен больше не принимается (sid в denylist).
    me = await service.client.get(
        "/v1/me", headers={"Authorization": f"Bearer {access_after_rotation}"}
    )
    assert_problem(me, 401, "auth.session_revoked")


async def test_logout_all_rejects_old_access(service: Any) -> None:
    await service.register()
    await service.login()
    first_access = service.client.cookies.get(ACCESS_COOKIE)
    await service.login()
    response = await service.client.post("/v1/auth/logout-all", headers=service.csrf())
    assert response.status_code == 204
    me = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {first_access}"})
    assert_problem(me, 401, "auth.session_revoked")


async def test_logout_clears_cookies(service: Any) -> None:
    await service.register()
    await service.login()
    access = service.client.cookies.get(ACCESS_COOKIE)
    response = await service.client.post("/v1/auth/logout", headers=service.csrf())
    assert response.status_code == 204
    cleared = _set_cookies(response)
    for name in ("gv_access", "gv_refresh", "gv_csrf"):
        assert "Max-Age=0" in cleared[name], cleared
    me = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {access}"})
    assert_problem(me, 401, "auth.session_revoked")


async def test_login_rate_limit_has_retry_after(service: Any) -> None:
    await service.register()
    for _ in range(5):
        await service.login("neo_2026", "wrong password!")
    response = await service.login()
    limited = assert_problem(response, 429, "rate.limited")
    retry_after = int(limited["details"]["retry_after"])
    assert retry_after > 60  # окно по аккаунту — 15 минут, а не общий Retry-After: 1
    assert int(response.headers["Retry-After"]) == retry_after


async def test_password_reset(service: Any) -> None:
    await service.register()
    await service.login()
    old_access = service.client.cookies.get(ACCESS_COOKIE)
    forgot = await service.client.post(
        "/v1/auth/password/forgot", json={"email": "neo@example.com"}, headers={"Origin": ORIGIN}
    )
    assert forgot.status_code == 202
    reset = await service.client.post(
        "/v1/auth/password/reset",
        json={"token": service.last_link_token(), "password": "a brand new passphrase"},
        headers={"Origin": ORIGIN},
    )
    assert reset.status_code == 204, reset.text
    assert (await service.login(password="a brand new passphrase")).status_code == 200
    me = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {old_access}"})
    assert_problem(me, 401, "auth.session_revoked")


# ----------------------------------------------------------------------------- CSRF


async def test_csrf_rules(service: Any) -> None:
    await service.register()
    await service.login()
    body = {"consents": [{"kind": "terms", "version": "2026-10"}]}
    no_header = await service.client.post("/v1/me/consents", json=body, headers={"Origin": ORIGIN})
    assert_problem(no_header, 403, "auth.csrf_failed")
    foreign = await service.client.post(
        "/v1/me/consents",
        json=body,
        headers={**service.csrf(), "Origin": "https://evil.test"},
    )
    assert_problem(foreign, 403, "auth.csrf_failed")
    assert (
        await service.client.post("/v1/me/consents", json=body, headers=service.csrf())
    ).status_code == 204

    # Bearer без CSRF проходит: браузер не подставляет заголовок Authorization сам.
    access = service.client.cookies.get(ACCESS_COOKIE)
    service.client.cookies.clear()
    bearer = await service.client.post(
        "/v1/me/consents", json=body, headers={"Authorization": f"Bearer {access}"}
    )
    assert bearer.status_code == 204


async def test_login_csrf_foreign_origin(service: Any) -> None:
    await service.register()
    response = await service.client.post(
        "/v1/auth/login",
        json={"login": "neo_2026", "password": PASSWORD},
        headers={"Origin": "https://evil.test"},
    )
    assert_problem(response, 403, "auth.csrf_failed")


async def test_refresh_without_csrf_is_forbidden(service: Any) -> None:
    await service.register()
    await service.login()
    response = await service.client.post("/v1/auth/refresh", headers={"Origin": ORIGIN})
    assert_problem(response, 403, "auth.csrf_failed")


# ----------------------------------------------------------------------------- Яндекс


def _mock_yandex(service: Any, subject: str) -> None:
    service.yandex.post(YA_TOKEN).respond(200, json={"access_token": f"tok-{subject}"})
    service.yandex.get(YA_INFO).respond(200, json={"id": subject, "login": "neo.ya"})


async def _start_link(service: Any) -> str:
    response = await service.client.post(
        "/v1/me/linked-accounts/yandex",
        json={"return_to": "/profile", "password": PASSWORD},
        headers=service.csrf(),
    )
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["authorize_url"])
    query = parse_qs(url.query)
    assert query["code_challenge_method"] == ["S256"]
    return query["state"][0]


async def test_link_yandex_and_login_through_it(service: Any) -> None:
    await service.register()
    await service.login()
    state = await _start_link(service)

    bad = await service.client.get(
        "/v1/oauth/yandex/callback", params={"state": "forged", "code": "c"}
    )
    assert_problem(bad, 400, "auth.oauth_state_invalid")

    _mock_yandex(service, "ya-1")
    done = await service.client.get(
        "/v1/oauth/yandex/callback", params={"state": state, "code": "c1"}
    )
    assert done.status_code == 302, done.text
    assert done.headers["location"] == "/profile?linked=yandex"
    linked = (await service.client.get("/v1/me/linked-accounts")).json()
    assert [a["provider"] for a in linked] == ["yandex"]

    # Повтор того же state невозможен.
    again = await service.client.get(
        "/v1/oauth/yandex/callback", params={"state": state, "code": "c1"}
    )
    assert_problem(again, 400, "auth.oauth_state_invalid")

    # Вход через привязанный Яндекс в «чистом» браузере.
    service.client.cookies.clear()
    start = await service.client.get("/v1/oauth/yandex/login", params={"return_to": "/play"})
    assert start.status_code == 302
    login_state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    done = await service.client.get(
        "/v1/oauth/yandex/callback", params={"state": login_state, "code": "c2"}
    )
    assert done.status_code == 302, done.text
    assert done.headers["location"] == "/play"
    assert (await service.client.get("/v1/me")).json()["nickname"] == "neo_2026"

    events = await service.events()
    linked_event = assert_published(events, "identity.ExternalAccountLinked")
    assert_event_matches_schema(linked_event)
    assert "ya-1" not in linked_event.to_bytes().decode()


async def test_yandex_account_of_other_user_is_conflict(service: Any) -> None:
    _mock_yandex(service, "ya-1")
    await service.register()
    await service.login()
    state = await _start_link(service)
    await service.client.get("/v1/oauth/yandex/callback", params={"state": state, "code": "c"})

    service.client.cookies.clear()
    await service.register("trinity", "trinity@example.com")
    await service.login("trinity")
    state = await _start_link(service)
    response = await service.client.get(
        "/v1/oauth/yandex/callback", params={"state": state, "code": "c"}
    )
    assert_problem(response, 409, "auth.provider_account_taken")


async def test_login_with_unlinked_yandex_is_conflict(service: Any) -> None:
    await service.register()
    _mock_yandex(service, "ya-unknown")
    start = await service.client.get("/v1/oauth/yandex/login")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = await service.client.get(
        "/v1/oauth/yandex/callback", params={"state": state, "code": "c"}
    )
    assert_problem(response, 409, "auth.provider_not_linked")


async def test_oauth_open_redirect_and_link_without_login(service: Any) -> None:
    evil = await service.client.get(
        "/v1/oauth/yandex/login", params={"return_to": "https://evil.test"}
    )
    assert_problem(evil, 400, "auth.return_to_invalid")
    anon = await service.client.post("/v1/me/linked-accounts/yandex", json={})
    assert_problem(anon, 401, "auth.unauthenticated")


# ----------------------------------------------------------------------------- admin и события


async def _admin_token(service: Any) -> str:
    await service.register("boss", "boss@example.com")  # bootstrap admin
    response = await service.client.post(
        "/internal/v1/admin-sessions", json={"login": "boss@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "admin.access" in body["user"]["perms"]
    return str(body["access_token"])


async def test_admin_session_requires_admin_access(service: Any) -> None:
    await service.register()
    response = await service.client.post(
        "/internal/v1/admin-sessions", json={"login": "neo_2026", "password": PASSWORD}
    )
    assert_problem(response, 403, "auth.forbidden")


async def test_admin_refresh(service: Any) -> None:
    await service.register("boss", "boss@example.com")
    first = await service.client.post(
        "/internal/v1/admin-sessions", json={"login": "boss", "password": PASSWORD}
    )
    refreshed = await service.client.post(
        "/internal/v1/admin-sessions/refresh",
        json={"refresh_token": first.json()["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != first.json()["refresh_token"]


async def test_assign_roles_publishes_valid_event(service: Any) -> None:
    token = await _admin_token(service)
    await service.register()
    auth = {"Authorization": f"Bearer {token}"}
    page = await service.client.get("/admin/v1/users", params={"q": "neo"}, headers=auth)
    assert page.status_code == 200, page.text
    (user,) = page.json()["items"]

    no_reason = await service.client.put(
        f"/admin/v1/users/{user['id']}/roles",
        json={"roles": ["moderator"], "reason": ""},
        headers={**auth, "Idempotency-Key": "key-no-reason"},
    )
    assert no_reason.status_code == 422
    response = await service.client.put(
        f"/admin/v1/users/{user['id']}/roles",
        json={"roles": ["moderator"], "reason": "помогает с каталогом"},
        headers={**auth, "Idempotency-Key": "key-assign-1"},
    )
    assert response.status_code == 200, response.text
    assert "catalog.block_item" in response.json()["perms"]

    events = await service.events()
    changed = assert_published(events, "identity.UserRolesChanged", {"user_id": user["id"]})
    assert_event_matches_schema(changed)
    assert changed.payload["reason"] == "помогает с каталогом"

    # Модератору admin API ролей недоступен.
    mod = await service.client.post(
        "/internal/v1/admin-sessions", json={"login": "neo_2026", "password": PASSWORD}
    )
    mod_auth = {"Authorization": f"Bearer {mod.json()['access_token']}"}
    assert_problem(
        await service.client.put(
            f"/admin/v1/users/{user['id']}/roles",
            json={"roles": ["admin"], "reason": "повышение"},
            headers={**mod_auth, "Idempotency-Key": "key-mod-1"},
        ),
        403,
        "auth.forbidden",
    )
    assert (await service.client.get("/admin/v1/users", headers=mod_auth)).status_code == 200


async def test_role_matrix_endpoints(service: Any) -> None:
    auth = {"Authorization": f"Bearer {await _admin_token(service)}"}
    matrix = (await service.client.get("/admin/v1/roles", headers=auth)).json()
    assert set(matrix["roles"]) == {"user", "moderator", "admin"}
    roles = {**matrix["roles"], "helper": ["users.view"]}
    response = await service.client.put(
        "/admin/v1/roles",
        json={"roles": roles, "reason": "новая роль"},
        headers={**auth, "Idempotency-Key": "key-matrix-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["roles"]["helper"] == ["users.view"]


async def test_jwks_has_both_keys_during_rotation(service: Any) -> None:
    response = await service.client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    keys = response.json()["keys"]
    assert {k["kid"] for k in keys} == {"k-old", "k-new"}
    assert all("d" not in k for k in keys)


async def test_all_events_valid_and_without_pii(service: Any) -> None:
    await _admin_token(service)
    await service.register()
    await service.login()
    await service.client.request(
        "DELETE", "/v1/me", json={"password": PASSWORD}, headers=service.csrf()
    )
    events = await service.events()
    types = [e.event_type for e in events]
    assert "identity.UserRegistered" in types
    assert "identity.UserRolesChanged" in types  # bootstrap admin
    assert "identity.UserDeleted" in types
    for envelope in events:
        assert_event_matches_schema(envelope)
        raw = envelope.to_bytes().decode()
        assert "@example.com" not in raw
        assert PASSWORD not in raw


async def test_no_pii_or_secrets_in_logs(service: Any) -> None:
    await service.register()
    await service.login()
    await service.login("neo_2026", "wrong password!")
    dump = str([{k: v for k, v in e.items() if k != "link"} for e in service.logs])
    assert "neo@example.com" not in dump
    assert PASSWORD not in dump
    assert "wrong password!" not in dump
    refresh = service.client.cookies.get("gv_refresh")
    assert refresh not in str(service.logs)


async def test_delete_me_frees_nickname(service: Any) -> None:
    await service.register()
    await service.login()
    wrong = await service.client.request(
        "DELETE", "/v1/me", json={"password": "nope nope nope"}, headers=service.csrf()
    )
    assert_problem(wrong, 401, "auth.invalid_credentials")
    ok = await service.client.request(
        "DELETE", "/v1/me", json={"password": PASSWORD}, headers=service.csrf()
    )
    assert ok.status_code == 204
    assert_problem(await service.login(), 401, "auth.invalid_credentials")
    service.client.cookies.clear()
    await service.register()  # ник и e-mail свободны
    assert (await service.login()).status_code == 200
