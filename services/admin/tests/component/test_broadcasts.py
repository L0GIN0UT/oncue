"""«Рассылки» (этап 5b, поток S4): список, создание в два шага, подтверждение.

Заглушка notifications проверяет ответы и запросы по contracts/openapi/notifications.json.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.apps.notifications.forms import BroadcastForm
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

NOW = "2026-10-23T10:00:00Z"
BROADCASTS = "/admin/v1/broadcasts"
ONLY = ("admin.access", "notifications.broadcast.send")


def _key() -> str:
    return str(uuid.uuid4())


def _broadcast(**patch: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "subject": "Обновление",
        "status": "queued",
        "recipients": 120,
        "created_at": NOW,
        "created_by": str(uuid.uuid4()),
    } | patch


def _form(**patch: Any) -> dict[str, Any]:
    return {
        "subject": "Обновление",
        "body": "Мы добавили магазин.\nЗаходите!",
        "reason": "анонс релиза",
        "idempotency_key": _key(),
    } | patch


def test_hidden_and_forbidden_without_perm(client: Any, login: Any) -> None:
    login(perms=("admin.access", "media.ugc.moderate"), roles=("moderator",))
    assert 'href="/broadcasts/"' not in client.get("/").content.decode()
    for path in ("", "new/"):
        assert client.get(f"/broadcasts/{path}").status_code == 403
    assert client.post("/broadcasts/new/", _form()).status_code == 403
    assert client.post("/broadcasts/send/", _form()).status_code == 403


def test_perm_opens_only_broadcasts(client: Any, login: Any, notifications: Any) -> None:
    login(perms=ONLY, roles=("moderator",))
    notifications.route("GET", BROADCASTS).respond(200, json={"items": [], "next_cursor": None})
    assert 'href="/broadcasts/"' in client.get("/").content.decode()
    assert client.get("/broadcasts/").status_code == 200
    assert client.get("/ugc/").status_code == 403


def test_list_shows_status_and_counts(client: Any, login: Any, notifications: Any) -> None:
    login()
    route = notifications.route("GET", BROADCASTS).respond(
        200, json={"items": [_broadcast(status="sending")], "next_cursor": "n1"}
    )
    page = client.get("/broadcasts/?cursor=c0").content.decode()
    assert "sending" in page and "120" in page and "cursor=n1" in page
    assert route.calls.last.request.url.params["cursor"] == "c0"


def test_list_survives_service_error(
    client: Any, login: Any, notifications: Any, problem: Any
) -> None:
    login()
    notifications.route("GET", BROADCASTS).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/broadcasts/")
    assert response.status_code == 200
    assert "Недостаточно прав" in response.content.decode()


def test_first_step_shows_confirmation_without_service_call(
    client: Any, login: Any, notifications: Any
) -> None:
    login()
    route = notifications.route("POST", BROADCASTS).respond(202, json=_broadcast())
    notifications.route("GET", f"{BROADCASTS}/audience").respond(200, json={"recipients": 1234})
    key = _key()
    response = client.post("/broadcasts/new/", _form(idempotency_key=key))
    page = response.content.decode()
    assert response.status_code == 200
    assert "Вы отправляете рассылку" in page
    assert "1234" in page  # число получателей из сервиса
    assert "Подтвердить и отправить" in page
    assert f'value="{key}"' in page  # тот же ключ уйдёт на втором шаге
    assert route.call_count == 0
    assert not AuditEntry.objects.exists()


def test_confirmed_send_calls_service_with_stable_key_and_audits(
    client: Any, login: Any, notifications: Any
) -> None:
    login()
    route = notifications.route("POST", BROADCASTS).respond(202, json=_broadcast())
    key = _key()
    data = _form(idempotency_key=key)
    response = client.post("/broadcasts/send/", data)
    assert response["Location"] == "/broadcasts/"
    client.post("/broadcasts/send/", data)  # повторная отправка той же формы
    assert {c.request.headers["Idempotency-Key"] for c in route.calls} == {key}
    assert json.loads(route.calls.last.request.content) == {
        "subject": "Обновление",
        "body": "Мы добавили магазин.\nЗаходите!",
        "reason": "анонс релиза",
    }
    entry = AuditEntry.objects.filter(action="notifications.broadcast").first()
    assert entry is not None
    assert (entry.service, entry.reason, entry.idempotency_key) == (
        "notifications",
        "анонс релиза",
        key,
    )
    assert entry.before == {"subject": "Обновление", "body_length": len(data["body"])}
    assert entry.after == {"id": entry.after["id"], "status": "queued", "recipients": 120}  # type: ignore[index]


def test_confirmation_survives_audience_error_without_blocking_send(
    client: Any, login: Any, notifications: Any, problem: Any
) -> None:
    login()
    notifications.route("GET", f"{BROADCASTS}/audience").respond(
        503, json=problem(503, "dependency.unavailable")
    )
    page = client.post("/broadcasts/new/", _form()).content.decode()
    assert "Подтвердить и отправить" in page
    assert "Число получателей сейчас недоступно" in page


@pytest.mark.parametrize("url", ["/broadcasts/new/", "/broadcasts/send/"])
def test_reason_required_on_both_steps(
    client: Any, login: Any, notifications: Any, url: str
) -> None:
    login()
    route = notifications.route("POST", BROADCASTS).respond(202, json=_broadcast())
    assert client.post(url, _form(reason="")).status_code == 400
    assert route.call_count == 0


@pytest.mark.parametrize("subject", ["строка\nвторая", "a\x00b", "tab\tтут", "del\x7fx"])
def test_subject_with_control_chars_is_rejected(
    client: Any, login: Any, notifications: Any, subject: str
) -> None:
    login()
    route = notifications.route("POST", BROADCASTS).respond(202, json=_broadcast())
    for url in ("/broadcasts/new/", "/broadcasts/send/"):
        assert client.post(url, _form(subject=subject)).status_code == 400
    assert route.call_count == 0


def test_subject_and_body_limits() -> None:
    assert not BroadcastForm(_form(subject="x" * 151)).is_valid()
    assert not BroadcastForm(_form(body="x" * 5001)).is_valid()
    assert BroadcastForm(_form(subject="x" * 150, body="x" * 5000)).is_valid()


@pytest.mark.parametrize(
    ("status", "code"), [(409, "resource.conflict"), (422, "request.validation_failed")]
)
def test_send_errors_are_messages(  # noqa: PLR0917
    client: Any, login: Any, notifications: Any, problem: Any, status: int, code: str
) -> None:
    login()
    notifications.route("POST", BROADCASTS).respond(status, json=problem(status, code))
    response = client.post("/broadcasts/send/", _form())
    assert response.status_code == 400
    entry = AuditEntry.objects.get(action="notifications.broadcast")
    assert (entry.outcome, entry.error_code) == (AuditEntry.Outcome.FAILED, code)


def test_send_503_and_429_are_messages(
    client: Any, login: Any, notifications: Any, problem: Any
) -> None:
    login()
    notifications.route("POST", BROADCASTS).respond_off_contract(
        503, json=problem(503, "dependency.unavailable")
    )
    page = client.post("/broadcasts/send/", _form()).content.decode()
    assert "временно недоступен" in page
    notifications.route("POST", BROADCASTS).respond_off_contract(
        429, json=problem(429, "rate.limited")
    )
    assert "Слишком много запросов" in client.post("/broadcasts/send/", _form()).content.decode()


def test_new_allows_only_get_and_post(client: Any, login: Any) -> None:
    login()
    assert client.put("/broadcasts/new/", _form()).status_code == 405
    assert client.delete("/broadcasts/new/").status_code == 405


def test_list_pagination_link_is_url_encoded(client: Any, login: Any, notifications: Any) -> None:
    login()
    notifications.route("GET", BROADCASTS).respond_off_contract(
        200, json={"items": [_broadcast()], "next_cursor": "c&x=1"}
    )
    assert "cursor=c%26x%3D1" in client.get("/broadcasts/").content.decode()
