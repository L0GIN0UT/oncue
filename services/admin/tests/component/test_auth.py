"""Вход в админку через identity, права, allowlist IP, health."""

from __future__ import annotations

import json
from typing import Any

import pytest
from admin_service.core.models import AdminUser

from gv_core.ids import uuid7
from gv_testkit import TestTokenIssuer

pytestmark = [pytest.mark.component, pytest.mark.django_db]


def test_login_via_identity_creates_mirror_with_perms(
    client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
) -> None:
    user_id = uuid7()
    token = token_issuer.issue(
        user_id=user_id, roles=["user", "moderator"], perms=["admin.access", "catalog.block_item"]
    )
    route = identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(token, user_id=user_id)
    )

    response = client.post("/login/", {"username": "mod@example.com", "password": "pw-123456"})

    assert response.status_code == 302
    assert json.loads(route.calls.last.request.content) == {
        "login": "mod@example.com",
        "password": "pw-123456",
        "client_ip": "127.0.0.1",
    }
    user = AdminUser.objects.get(identity_user_id=user_id)
    assert user.is_staff and not user.is_superuser
    assert not user.has_usable_password()
    assert user.display_name == "Модератор"
    assert user.has_perm("identity.catalog.block_item")
    assert not user.has_perm("identity.catalog.import")
    assert sorted(g.name for g in user.groups.all()) == ["identity:moderator", "identity:user"]
    # Токены — в серверной сессии, а не в cookie.
    assert token not in response.cookies.output()


def test_admin_role_becomes_superuser(login: Any) -> None:
    user_id = login(roles=("admin",))
    assert AdminUser.objects.get(identity_user_id=user_id).is_superuser


def test_wrong_password_shows_form_error(client: Any, identity: Any) -> None:
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        401, json={"status": 401, "code": "auth.invalid_credentials", "title": "Unauthorized"}
    )
    response = client.post("/login/", {"username": "mod", "password": "bad-password"})
    assert response.status_code == 200
    assert not AdminUser.objects.exists()


def test_identity_forbids_without_admin_access(client: Any, identity: Any) -> None:
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        403, json={"status": 403, "code": "auth.forbidden", "title": "Forbidden"}
    )
    response = client.post("/login/", {"username": "player", "password": "pw-123456"})
    assert response.status_code == 200
    assert not AdminUser.objects.exists()


def test_token_without_admin_access_is_rejected_even_if_identity_said_ok(
    client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
) -> None:
    user_id = uuid7()
    token = token_issuer.issue(user_id=user_id, perms=["catalog.block_item"])
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(token, user_id=user_id)
    )
    response = client.post("/login/", {"username": "mod", "password": "pw-123456"})
    assert response.status_code == 200
    assert not AdminUser.objects.exists()


def test_forged_token_is_rejected(client: Any, identity: Any, session_body: Any) -> None:
    user_id = uuid7()
    forged = TestTokenIssuer().issue(user_id=user_id, perms=["admin.access"])  # чужой ключ
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(forged, user_id=user_id)
    )
    response = client.post("/login/", {"username": "mod", "password": "pw-123456"})
    assert response.status_code == 200
    assert not AdminUser.objects.exists()


def test_identity_unavailable_is_reported(client: Any, identity: Any) -> None:
    identity.route("POST", "/internal/v1/admin-sessions").respond(503, json={"status": 503})
    response = client.post("/login/", {"username": "mod", "password": "pw-123456"})
    assert response.status_code == 200
    assert "Вход временно невозможен" in response.content.decode()


def test_anonymous_is_redirected_to_login(client: Any) -> None:
    response = client.get("/catalog/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


def test_missing_perm_is_403(client: Any, login: Any) -> None:
    login(perms=("admin.access", "catalog.block_item"), roles=("moderator",))
    assert client.get("/catalog/imports/new/").status_code == 403
    assert client.get("/users/").status_code == 403


def test_deactivated_mirror_is_logged_out(client: Any, login: Any, catalog: Any) -> None:
    user_id = login()
    AdminUser.objects.filter(identity_user_id=user_id).update(is_active=False)
    response = client.get("/catalog/")
    assert response.status_code == 302


def test_expired_access_is_refreshed(
    client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any, catalog: Any
) -> None:
    user_id = uuid7()
    perms = ("admin.access", "catalog.block_item")
    first = token_issuer.issue(user_id=user_id, roles=["moderator"], perms=perms)
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(first, user_id=user_id, expires_in=0)
    )
    assert client.post("/login/", {"username": "m", "password": "pw-123456"}).status_code == 302
    second = token_issuer.issue(user_id=user_id, roles=["moderator"], perms=(*perms, "users.view"))
    refresh = identity.route("POST", "/internal/v1/admin-sessions/refresh").respond(
        200, json=session_body(second, user_id=user_id, refresh="r2")
    )
    items = catalog.route("GET", "/admin/v1/items").respond(
        200, json={"items": [], "next_cursor": None}
    )

    assert client.get("/catalog/").status_code == 200
    assert json.loads(refresh.calls.last.request.content) == {"refresh_token": "r1"}
    assert items.calls.last.request.headers["Authorization"] == f"Bearer {second}"
    # Права синхронизируются и на refresh.
    assert AdminUser.objects.get(identity_user_id=user_id).has_perm("identity.users.view")


def test_rejected_refresh_logs_out(
    client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
) -> None:
    user_id = uuid7()
    token = token_issuer.issue(user_id=user_id, perms=["admin.access"])
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(token, user_id=user_id, expires_in=0)
    )
    client.post("/login/", {"username": "m", "password": "pw-123456"})
    identity.route("POST", "/internal/v1/admin-sessions/refresh").respond(
        401, json={"status": 401, "code": "auth.refresh_reused"}
    )
    response = client.get("/catalog/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")
    assert "_auth_user_id" not in client.session


class TestIpAllowlist:
    def test_foreign_ip_is_denied(self, client: Any) -> None:
        response = client.get("/login/", REMOTE_ADDR="203.0.113.7")
        assert response.status_code == 403

    def test_allowed_ip_passes(self, client: Any) -> None:
        assert client.get("/login/", REMOTE_ADDR="127.0.0.1").status_code == 200

    def test_health_bypasses_allowlist_and_host_check(self, client: Any) -> None:
        response = client.get("/health/live", REMOTE_ADDR="203.0.113.7", HTTP_HOST="10.1.2.3")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_checks_postgres(self, client: Any) -> None:
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "checks": {"postgres": True}}


def test_request_id_is_propagated(client: Any) -> None:
    rid = str(uuid7())
    assert client.get("/login/", HTTP_X_REQUEST_ID=rid)["X-Request-Id"] == rid
    garbage = client.get("/login/", HTTP_X_REQUEST_ID="<script>")["X-Request-Id"]
    assert garbage != "<script>"


def _stored_session() -> dict[str, Any]:
    from django.contrib.sessions.models import Session

    (row,) = Session.objects.all()
    decoded: dict[str, Any] = row.get_decoded()
    return decoded


def test_tokens_are_not_stored_in_plaintext(
    client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
) -> None:
    user_id = uuid7()
    token = token_issuer.issue(user_id=user_id, perms=["admin.access"])
    refresh = f"refresh-{uuid7().hex}"
    identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(token, user_id=user_id, refresh=refresh)
    )
    assert client.post("/login/", {"username": "m", "password": "pw-123456"}).status_code == 302

    dumped = json.dumps(_stored_session())
    assert refresh not in dumped
    assert token not in dumped


def test_legacy_plaintext_session_requires_new_login(client: Any, login: Any, catalog: Any) -> None:
    login()
    session = client.session
    session["gv_identity"] = {"access": "a", "refresh": "r", "expires_at": 9e12}
    session.save()
    response = client.get("/catalog/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


class TestIdentityVersion:
    """Вход и refresh поднимают identity_version: старое событие не откатит права."""

    def _login(
        self, client: Any, identity: Any, issuer: TestTokenIssuer, body: Any, **kw: Any
    ) -> Any:
        user_id = kw.pop("user_id", uuid7())
        token = issuer.issue(user_id=user_id, roles=["admin"], perms=["admin.access"])
        identity.route("POST", "/internal/v1/admin-sessions").respond(
            200, json=body(token, user_id=user_id, **kw)
        )
        assert client.post("/login/", {"username": "m", "password": "pw-123456"}).status_code == 302
        return user_id

    def test_stale_event_after_login_does_not_roll_back(
        self, client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
    ) -> None:
        from admin_service.core.events import handle_envelope

        from gv_testkit.contracts import load_example

        user_id = self._login(client, identity, token_issuer, session_body, identity_version=5)
        assert AdminUser.objects.get(identity_user_id=user_id).identity_version == 5
        example = load_example("identity.UserRolesChanged", fresh_id=True)
        stale = example.model_copy(
            update={
                "payload": {**example.payload, "user_id": str(user_id), "version": 4, "perms": []}
            }
        )
        handle_envelope(stale)
        user = AdminUser.objects.get(identity_user_id=user_id)
        assert user.is_staff and user.is_superuser  # права из входа не откатились

    def test_version_never_decreases(
        self, client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
    ) -> None:
        user_id = uuid7()
        AdminUser.objects.create(
            username=f"identity-{user_id}", identity_user_id=user_id, identity_version=7
        )
        self._login(
            client, identity, token_issuer, session_body, user_id=user_id, identity_version=5
        )
        assert AdminUser.objects.get(identity_user_id=user_id).identity_version == 7

    def test_refresh_raises_version(
        self,
        client: Any,
        identity: Any,
        token_issuer: TestTokenIssuer,
        session_body: Any,
        catalog: Any,
    ) -> None:
        user_id = self._login(
            client, identity, token_issuer, session_body, identity_version=1, expires_in=0
        )
        token = token_issuer.issue(user_id=user_id, roles=["admin"], perms=["admin.access"])
        identity.route("POST", "/internal/v1/admin-sessions/refresh").respond(
            200, json=session_body(token, user_id=user_id, refresh="r2", identity_version=9)
        )
        catalog.route("GET", "/admin/v1/items").respond(
            200, json={"items": [], "next_cursor": None}
        )
        assert client.get("/catalog/").status_code == 200
        assert AdminUser.objects.get(identity_user_id=user_id).identity_version == 9


class TestLoginThrottle:
    def test_attempts_over_limit_are_rejected_without_identity(
        self, client: Any, identity: Any, settings: Any
    ) -> None:
        settings.GV_LOGIN_ATTEMPTS_LIMIT = 2
        route = identity.route("POST", "/internal/v1/admin-sessions").respond(
            401, json={"status": 401, "code": "auth.invalid_credentials", "title": "Unauthorized"}
        )
        for _ in range(2):
            assert client.post("/login/", {"username": "m", "password": "bad"}).status_code == 200
        response = client.post("/login/", {"username": "m", "password": "bad"})
        assert response.status_code == 429
        assert response["Retry-After"] == str(settings.GV_LOGIN_ATTEMPTS_WINDOW_S)
        assert route.call_count == 2

    def test_limit_is_per_ip(self, client: Any, identity: Any, settings: Any) -> None:
        settings.GV_LOGIN_ATTEMPTS_LIMIT = 1
        settings.GV_IP_ALLOWLIST = ["127.0.0.0/8"]
        identity.route("POST", "/internal/v1/admin-sessions").respond(
            401, json={"status": 401, "code": "auth.invalid_credentials", "title": "Unauthorized"}
        )
        data = {"username": "m", "password": "bad"}
        assert client.post("/login/", data, REMOTE_ADDR="127.0.0.2").status_code == 200
        assert client.post("/login/", data, REMOTE_ADDR="127.0.0.2").status_code == 429
        assert client.post("/login/", data, REMOTE_ADDR="127.0.0.3").status_code == 200

    def test_successful_login_does_not_consume_limit(self, login: Any, settings: Any) -> None:
        settings.GV_LOGIN_ATTEMPTS_LIMIT = 1
        login()
        login()  # второй вход с того же IP — не 429

    def test_login_page_get_is_not_counted(self, client: Any, settings: Any) -> None:
        settings.GV_LOGIN_ATTEMPTS_LIMIT = 1
        for _ in range(3):
            assert client.get("/login/").status_code == 200


class TestLogout:
    def test_logout_revokes_identity_session(
        self, client: Any, identity: Any, token_issuer: TestTokenIssuer, session_body: Any
    ) -> None:
        user_id = uuid7()
        token = token_issuer.issue(user_id=user_id, perms=["admin.access"])
        identity.route("POST", "/internal/v1/admin-sessions").respond(
            200, json=session_body(token, user_id=user_id, refresh="r-logout")
        )
        client.post("/login/", {"username": "m", "password": "pw-123456"})
        revoke = identity.route("POST", "/internal/v1/admin-sessions/revoke").respond(204)

        response = client.post("/logout/")

        assert response.status_code == 200
        assert json.loads(revoke.calls.last.request.content) == {"refresh_token": "r-logout"}
        assert "_auth_user_id" not in client.session

    @pytest.mark.parametrize("failure", ["status", "network"])
    def test_logout_succeeds_even_if_revoke_fails(
        self, client: Any, identity: Any, login: Any, failure: str
    ) -> None:
        import httpx

        login()
        revoke = identity.route("POST", "/internal/v1/admin-sessions/revoke")
        if failure == "status":
            revoke.respond(503, json={"status": 503})
        else:
            revoke.mock(side_effect=httpx.ConnectError("down"))
        response = client.post("/logout/")
        assert response.status_code == 200
        assert revoke.called
        assert "_auth_user_id" not in client.session

    def test_get_does_not_revoke(self, client: Any, identity: Any, login: Any) -> None:
        login()
        revoke = identity.route("POST", "/internal/v1/admin-sessions/revoke").respond(204)
        assert client.get("/logout/").status_code == 405
        assert not revoke.called
