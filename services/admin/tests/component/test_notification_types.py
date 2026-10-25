"""«Типы уведомлений» (ADR 0066): список и переключатель с обязательной причиной.

Заглушка notifications проверяет ответы и запросы по contracts/openapi/notifications.json.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

NOW = "2026-10-25T10:00:00Z"
TYPES = "/admin/v1/notification-types"
ONLY = ("admin.access", "notifications.types.manage")


def _key() -> str:
    return str(uuid.uuid4())


def _item(type_key: str = "level_up", **patch: Any) -> dict[str, Any]:
    return {
        "type": type_key,
        "enabled": True,
        "default_enabled": True,
        "changed_by": None,
        "changed_at": None,
        "switchable": True,
    } | patch


def _form(**patch: Any) -> dict[str, Any]:
    return {
        "type": "level_up",
        "enabled": "false",
        "reason": "тип шумит, отключаем",
        "idempotency_key": _key(),
    } | patch


def test_hidden_and_forbidden_without_perm(client: Any, login: Any) -> None:
    login(perms=("admin.access", "notifications.broadcast.send"), roles=("moderator",))
    assert 'href="/notification-types/"' not in client.get("/").content.decode()
    assert client.get("/notification-types/").status_code == 403
    assert client.post("/notification-types/set/", _form()).status_code == 403


def test_perm_opens_only_types(client: Any, login: Any, notifications: Any) -> None:
    login(perms=ONLY, roles=("moderator",))
    notifications.route("GET", TYPES).respond(200, json={"items": []})
    assert 'href="/notification-types/"' in client.get("/").content.decode()
    assert client.get("/notification-types/").status_code == 200
    assert client.get("/broadcasts/").status_code == 403


def test_list_shows_state_and_who_changed(client: Any, login: Any, notifications: Any) -> None:
    login()
    admin = str(uuid.uuid4())
    notifications.route("GET", TYPES).respond(
        200,
        json={
            "items": [
                _item("level_up", enabled=False, changed_by=admin, changed_at=NOW),
                _item("badge_unlocked"),
            ]
        },
    )
    page = client.get("/notification-types/").content.decode()
    assert "level_up" in page and "badge_unlocked" in page
    assert admin in page and "Выключен" in page and "Включён" in page


def test_list_survives_service_error(
    client: Any, login: Any, notifications: Any, problem: Any
) -> None:
    login()
    notifications.route("GET", TYPES).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/notification-types/")
    assert response.status_code == 200
    assert "Недостаточно прав" in response.content.decode()


def test_toggle_calls_service_with_stable_key_and_audits(
    client: Any, login: Any, notifications: Any
) -> None:
    login()
    route = notifications.route("PUT", f"{TYPES}/level_up").respond(
        200, json=_item(enabled=False, changed_by=str(uuid.uuid4()), changed_at=NOW)
    )
    key = _key()
    data = _form(idempotency_key=key)
    response = client.post("/notification-types/set/", data)
    assert response["Location"] == "/notification-types/"
    client.post("/notification-types/set/", data)  # повторная отправка той же формы
    assert {c.request.headers["Idempotency-Key"] for c in route.calls} == {key}
    assert json.loads(route.calls.last.request.content) == {
        "enabled": False,
        "reason": "тип шумит, отключаем",
    }
    entry = AuditEntry.objects.filter(action="notifications.type_toggle").first()
    assert entry is not None
    assert (entry.service, entry.target_id, entry.reason, entry.idempotency_key) == (
        "notifications",
        "level_up",
        "тип шумит, отключаем",
        key,
    )
    assert entry.after == {"type": "level_up", "enabled": False}


def test_enable_sends_true(client: Any, login: Any, notifications: Any) -> None:
    login()
    route = notifications.route("PUT", f"{TYPES}/level_up").respond(200, json=_item())
    client.post("/notification-types/set/", _form(enabled="true"))
    assert json.loads(route.calls.last.request.content)["enabled"] is True


@pytest.mark.parametrize("patch", [{"reason": ""}, {"reason": "ab"}, {"type": "Bad Type!"}])
def test_invalid_form_does_not_call_service(
    client: Any, login: Any, notifications: Any, patch: dict[str, Any]
) -> None:
    login()
    route = notifications.route("PUT", f"{TYPES}/level_up").respond(200, json=_item())
    assert client.post("/notification-types/set/", _form(**patch)).status_code == 400
    assert route.call_count == 0
    assert not AuditEntry.objects.exists()


def test_service_error_is_message_and_audited(
    client: Any, login: Any, notifications: Any, problem: Any
) -> None:
    login()
    notifications.route("PUT", f"{TYPES}/level_up").respond(
        404, json=problem(404, "notifications.type_not_found")
    )
    response = client.post("/notification-types/set/", _form())
    assert response.status_code == 302
    entry = AuditEntry.objects.get(action="notifications.type_toggle")
    assert (entry.outcome, entry.error_code) == (
        AuditEntry.Outcome.FAILED,
        "notifications.type_not_found",
    )


def test_set_allows_only_post(client: Any, login: Any) -> None:
    login()
    assert client.get("/notification-types/set/").status_code == 405


def test_system_type_has_no_toggle(client: Any, login: Any, notifications: Any) -> None:
    login()
    notifications.route("GET", TYPES).respond(
        200,
        json={
            "items": [
                _item("level_up", switchable=True),
                _item("avatar_approved", switchable=False),
            ]
        },
    )
    page = client.get("/notification-types/").content.decode()
    assert 'value="level_up"' in page
    assert 'value="avatar_approved"' not in page
    assert "avatar_approved" in page
