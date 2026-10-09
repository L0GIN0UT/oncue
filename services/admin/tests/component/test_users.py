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
SUBJECT_JSON = {
    "id": str(SUBJECT),
    "nickname": "neo",
    "display_name": "Нео",
    "status": "active",
    "roles": ["user"],
}


def test_search_and_card(client: Any, login: Any, identity: Any) -> None:
    login(perms=("admin.access", "users.view"), roles=("moderator",))
    search = identity.route("GET", "/admin/v1/users").respond(200, json={"items": [SUBJECT_JSON]})
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
    put = identity.route("PUT", f"/admin/v1/users/{SUBJECT}/roles").respond(200, json={})
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
    entry = AuditEntry.objects.get()
    assert (entry.action, entry.before) == ("identity.assign_roles", {"roles": ["user"]})


def test_assign_roles_requires_perm(client: Any, login: Any, identity: Any) -> None:
    login(perms=("admin.access", "users.view"), roles=("moderator",))
    put = identity.route("PUT", f"/admin/v1/users/{SUBJECT}/roles").respond(200, json={})
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
