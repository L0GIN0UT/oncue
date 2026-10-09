"""Раздел «Пользователи»: поиск, карточка, смена ролей через admin-API identity."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

from gv_core.ids import uuid7

pytestmark = [pytest.mark.component, pytest.mark.django_db]

SUBJECT = uuid7()
EMAIL = "neo@example.com"
# AdminUser из contracts/openapi/identity.json (заглушка проверяет схему в conftest).
SUBJECT_JSON = {
    "id": str(SUBJECT),
    "nickname": "neo",
    "email": EMAIL,
    "email_verified": True,
    "display_name": "Нео",
    "status": "active",
    "roles": ["user"],
    "perms": [],
    "created_at": "2026-01-01T00:00:00Z",
    "linked_providers": [],
}


def test_search_and_card(client: Any, login: Any, identity: Any) -> None:
    login(perms=("admin.access", "users.view"), roles=("moderator",))
    search = identity.route("GET", "/admin/v1/users").respond(
        200, json={"items": [SUBJECT_JSON], "next_cursor": None}
    )
    identity.route("GET", f"/admin/v1/users/{SUBJECT}").respond(200, json=SUBJECT_JSON)
    listing = client.get("/users/", {"q": "neo"})
    assert f"/users/{SUBJECT}/" in listing.content.decode()
    assert dict(search.calls.last.request.url.params) == {"q": "neo"}
    card = client.get(f"/users/{SUBJECT}/")
    assert "Нео" in card.content.decode()
    assert "Сохранить роли" not in card.content.decode()  # нет roles.assign — нет формы


def test_assign_roles(client: Any, login: Any, identity: Any) -> None:
    login()
    identity.route("GET", f"/admin/v1/users/{SUBJECT}").respond(200, json=SUBJECT_JSON)
    put = identity.route("PUT", f"/admin/v1/users/{SUBJECT}/roles").respond(
        200, json={**SUBJECT_JSON, "roles": ["moderator", "user"]}
    )
    key = str(uuid.uuid4())
    response = client.post(
        f"/users/{SUBJECT}/roles/",
        {
            "roles": "moderator, user, moderator",
            "reason": "новый модератор",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 302
    request = put.calls.last.request
    assert json.loads(request.content) == {
        "roles": ["moderator", "user"],
        "reason": "новый модератор",
    }
    assert request.headers["Idempotency-Key"] == key
    entry = AuditEntry.objects.get(action="identity.assign_roles")
    assert entry.before == {"roles": ["user"]}
    # В неизменяемый журнал — только изменённые поля, без e-mail и остального ответа.
    assert entry.after == {"roles": ["moderator", "user"]}


def test_assign_roles_requires_perm(client: Any, login: Any, identity: Any) -> None:
    login(perms=("admin.access", "users.view"), roles=("moderator",))
    put = identity.route("PUT", f"/admin/v1/users/{SUBJECT}/roles").respond(200, json=SUBJECT_JSON)
    response = client.post(
        f"/users/{SUBJECT}/roles/",
        {"roles": "admin", "reason": "хочу", "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 403
    assert not put.called


def test_invalid_role_name_is_form_error(client: Any, login: Any, identity: Any) -> None:
    login()
    identity.route("GET", f"/admin/v1/users/{SUBJECT}").respond(200, json=SUBJECT_JSON)
    response = client.post(
        f"/users/{SUBJECT}/roles/",
        {"roles": "Admin;drop", "reason": "причина", "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 400


class TestReadAudit:
    """Просмотр данных пользователей с e-mail (users.view) пишется в аудит — без e-mail."""

    def test_search_is_audited_without_email(self, client: Any, login: Any, identity: Any) -> None:
        actor = login(perms=("admin.access", "users.view"), roles=("moderator",))
        identity.route("GET", "/admin/v1/users").respond(
            200, json={"items": [SUBJECT_JSON], "next_cursor": None}
        )
        client.get("/users/", {"q": EMAIL})
        entry = AuditEntry.objects.get(action="identity.view_users")
        assert entry.actor_identity_id == actor
        assert entry.outcome == "succeeded"
        assert entry.after == {"q": "<e-mail>", "user_ids": [str(SUBJECT)]}
        assert EMAIL not in json.dumps([entry.after, entry.before, entry.target_id])

    def test_card_view_is_audited(self, client: Any, login: Any, identity: Any) -> None:
        login(perms=("admin.access", "users.view"), roles=("moderator",))
        identity.route("GET", f"/admin/v1/users/{SUBJECT}").respond(200, json=SUBJECT_JSON)
        client.get(f"/users/{SUBJECT}/")
        entry = AuditEntry.objects.get(action="identity.view_user")
        assert (entry.target_type, entry.target_id, entry.after) == ("user", str(SUBJECT), None)

    def test_failed_read_is_not_audited(self, client: Any, login: Any, identity: Any) -> None:
        login(perms=("admin.access", "users.view"), roles=("moderator",))
        identity.route("GET", "/admin/v1/users").respond(503, json={"status": 503})
        client.get("/users/")
        assert not AuditEntry.objects.exists()


def test_search_query_is_limited_to_contract(client: Any, login: Any, identity: Any) -> None:
    login(perms=("admin.access", "users.view"), roles=("moderator",))
    search = identity.route("GET", "/admin/v1/users").respond(
        200, json={"items": [], "next_cursor": None}
    )
    client.get("/users/", {"q": "x" * 65})  # identity принимает q до 64 символов
    assert "q" not in dict(search.calls.last.request.url.params)
