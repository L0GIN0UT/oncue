"""«События» (этап 5d, поток E2): определение, карта, запуски, публикация, остановка, статистика.

Заглушка progression проверяет ответы по contracts/openapi/progression.json и запросы админки
(JSON/multipart по схемам, ``Idempotency-Key`` у мутаций).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = [pytest.mark.component, pytest.mark.django_db]

NOW = "2026-12-01T10:00:00Z"
RUN = str(uuid.uuid4())
SHA = "b" * 64
EVENTS = "/admin/v1/events"
KEY = "new_year_2026"
MANAGE_ONLY = ("admin.access", "progression.events.manage")
_EXAMPLE = Path(__file__).resolve().parents[4] / "contracts" / "progression" / "examples"
BODY = json.loads((_EXAMPLE / "event_definition.v1.json").read_text("utf-8"))


def _key() -> str:
    return str(uuid.uuid4())


def _run(**patch: Any) -> dict[str, Any]:
    return {
        "id": RUN,
        "seq": 1,
        "status": "draft",
        "starts_at": "2026-12-20T00:00:00Z",
        "ends_at": "2027-01-10T00:00:00Z",
        "published_at": None,
        "stopped_at": None,
    } | patch


def _event(*runs: dict[str, Any], map_url: str | None = None) -> dict[str, Any]:
    return {
        "key": KEY,
        "body": BODY,
        "body_sha256": SHA,
        "map_url": map_url,
        "runs": list(runs),
        "updated_at": NOW,
    }


def _stub_event(progression: Any, *runs: dict[str, Any], **kw: Any) -> None:
    progression.route("GET", f"{EVENTS}/{KEY}").respond(200, json=_event(*runs, **kw))


def _editor(**patch: Any) -> dict[str, Any]:
    return {
        "fmt": "json",
        "text": json.dumps(BODY, ensure_ascii=False),
        "reason": "правка",
        "idempotency_key": _key(),
    } | patch


# ------------------------------------------------------------------------------ доступ


def test_section_hidden_and_forbidden_without_perms(client: Any, login: Any) -> None:
    login(perms=("admin.access", "progression.items.manage"), roles=("moderator",))
    assert 'href="/events/"' not in client.get("/").content.decode()
    for path in ("", "new/", f"{KEY}/", f"{KEY}/edit/", f"{KEY}/runs/{RUN}/stats/"):
        assert client.get(f"/events/{path}").status_code == 403
    assert client.post(f"/events/{KEY}/runs/{RUN}/publish/", {}).status_code == 403
    assert not AuditEntry.objects.exists()


def test_manage_without_publish_cannot_publish_or_stop(
    client: Any, login: Any, progression: Any
) -> None:
    login(perms=MANAGE_ONLY, roles=("moderator",))
    assert 'href="/events/"' in client.get("/").content.decode()
    _stub_event(progression, _run(), _run(id=str(uuid.uuid4()), seq=2, status="active"))
    page = client.get(f"/events/{KEY}/").content.decode()
    assert "Опубликовать" not in page
    assert "Остановить" not in page
    assert client.post(f"/events/{KEY}/runs/{RUN}/stop/", {}).status_code == 403
    assert not AuditEntry.objects.exists()


# ------------------------------------------------------------------------------ список, карточка


def test_list_shows_events(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", EVENTS).respond(
        200,
        json={
            "items": [{"key": KEY, "title": "Новый год", "status": "active", "latest_run": _run()}]
        },
    )
    page = client.get("/events/").content.decode()
    assert KEY in page
    assert "Новый год" in page


def test_card_previews_find_points_on_map(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression, _run(), map_url="https://cdn.test/map.png")
    page = client.get(f"/events/{KEY}/").content.decode()
    assert "left:12.5%;top:40.0%" in page or "left:12.5%;top:40%" in page
    assert "https://cdn.test/map.png" in page
    assert "Первый лист" in page
    assert SHA in page


def test_card_of_missing_event_is_404(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("GET", f"{EVENTS}/{KEY}").respond(
        404, json=problem(404, "progression.event_not_found")
    )
    assert client.get(f"/events/{KEY}/").status_code == 404


# ------------------------------------------------------------------------------ определение


def test_create_event_sends_key_and_body_and_audits(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    route = progression.route("POST", EVENTS).respond(201, json=_event())
    key = _key()
    response = client.post(
        "/events/new/", _editor(key=KEY, idempotency_key=key, reason="новое событие")
    )
    assert response["Location"] == f"/events/{KEY}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    sent = json.loads(request.content)
    assert (sent["key"], sent["body"]["theme_key"], sent["reason"]) == (
        KEY,
        "autumn",
        "новое событие",
    )
    entry = AuditEntry.objects.get(action="events.create")
    assert (entry.service, entry.target_id) == ("progression", KEY)
    assert entry.after == {"key": KEY, "body_sha256": SHA}  # тела определения в аудите нет


def test_create_event_requires_reason_and_valid_key(client: Any, login: Any) -> None:
    login()
    assert client.post("/events/new/", _editor(key=KEY, reason="")).status_code == 400
    assert client.post("/events/new/", _editor(key="Bad-Key")).status_code == 400
    assert not AuditEntry.objects.exists()


def test_invalid_definition_shows_error_table_with_lines(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    error = problem(422, "progression.event_definition_invalid") | {
        "details": {"errors": [{"path": "finds.0.x_pct", "reason": "больше 100"}]}
    }
    progression.route("POST", EVENTS).respond(422, json=error)
    response = client.post("/events/new/", _editor(key=KEY))
    assert response.status_code == 400
    page = response.content.decode()
    assert "finds.0.x_pct" in page
    assert "больше 100" in page


def test_edit_locked_definition_shows_message_and_audit(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_event(progression, _run(status="published"))
    progression.route("PUT", f"{EVENTS}/{KEY}").respond(
        409, json=problem(409, "progression.event_definition_locked")
    )
    response = client.post(f"/events/{KEY}/edit/", _editor())
    assert response.status_code == 400
    assert "Определение нельзя менять" in response.content.decode()
    entry = AuditEntry.objects.get(action="events.update")
    assert entry.error_code == "progression.event_definition_locked"


def test_edit_saves_with_idempotency_key(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    route = progression.route("PUT", f"{EVENTS}/{KEY}").respond(200, json=_event())
    key = _key()
    response = client.post(f"/events/{KEY}/edit/", _editor(idempotency_key=key))
    assert response["Location"] == f"/events/{KEY}/"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert AuditEntry.objects.get(action="events.update").before == {"body_sha256": SHA}


# ------------------------------------------------------------------------------ карта


def test_map_upload_is_multipart_and_audited_without_content(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    route = progression.route("PUT", f"{EVENTS}/{KEY}/map").respond(200, json=_event())
    upload = SimpleUploadedFile("map.png", b"\x89PNG-data", content_type="image/png")
    response = client.post(
        f"/events/{KEY}/map/", {"file": upload, "reason": "карта", "idempotency_key": _key()}
    )
    assert response["Location"] == f"/events/{KEY}/"
    assert b'filename="map.png"' in route.calls.last.request.content
    entry = AuditEntry.objects.get(action="events.upload_map")
    assert entry.after == {"bytes": 9, "content_type": "image/png"}


def test_map_invalid_shows_message(client: Any, login: Any, progression: Any, problem: Any) -> None:
    login()
    _stub_event(progression)
    progression.route("PUT", f"{EVENTS}/{KEY}/map").respond(
        422, json=problem(422, "progression.event_map_invalid")
    )
    upload = SimpleUploadedFile("map.png", b"x", content_type="image/png")
    response = client.post(
        f"/events/{KEY}/map/", {"file": upload, "reason": "карта", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert "Карта не подходит" in response.content.decode()


# ------------------------------------------------------------------------------ запуски


def test_create_run_sends_utc_period(client: Any, login: Any, progression: Any) -> None:
    login()
    route = progression.route("POST", f"{EVENTS}/{KEY}/runs").respond(201, json=_run())
    key = _key()
    response = client.post(
        f"/events/{KEY}/runs/new/",
        {
            "starts_at": "2026-12-20T00:00",
            "ends_at": "2027-01-10T12:30",
            "reason": "новый год",
            "idempotency_key": key,
        },
    )
    assert response["Location"] == f"/events/{KEY}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    sent = json.loads(request.content)
    assert (sent["starts_at"], sent["ends_at"]) == ("2026-12-20T00:00:00Z", "2027-01-10T12:30:00Z")
    assert AuditEntry.objects.get(action="events.create_run").target_id == KEY


def test_create_run_rejects_end_before_start(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    response = client.post(
        f"/events/{KEY}/runs/new/",
        {
            "starts_at": "2027-01-10T00:00",
            "ends_at": "2026-12-20T00:00",
            "reason": "ошибка",
            "idempotency_key": _key(),
        },
    )
    assert response.status_code == 400
    assert "Конец периода должен быть позже начала" in response.content.decode()
    assert not AuditEntry.objects.exists()


def test_publish_requires_confirmation_and_sends_seen_hash(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression, _run())
    page = client.get(f"/events/{KEY}/").content.decode()
    assert f'value="{SHA}"' in page  # хеш уходит скрытым полем из карточки
    route = progression.route("POST", f"{EVENTS}/{KEY}/runs/{RUN}/publish").respond(
        200, json=_run(status="published", published_at=NOW)
    )
    data = {"body_sha256": SHA, "reason": "публикуем", "idempotency_key": _key()}
    unconfirmed = client.post(f"/events/{KEY}/runs/{RUN}/publish/", data)
    assert unconfirmed.status_code == 400
    assert route.call_count == 0
    key = _key()
    response = client.post(
        f"/events/{KEY}/runs/{RUN}/publish/", data | {"confirm": "on", "idempotency_key": key}
    )
    assert response["Location"] == f"/events/{KEY}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content)["body_sha256"] == SHA
    entry = AuditEntry.objects.get(action="events.publish_run")
    assert (entry.target_id, entry.before) == (RUN, {"event_key": KEY, "body_sha256": SHA})


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("progression.event_body_changed", "Определение изменили"),
        ("progression.event_period_overlap", "пересекается"),
    ],
)
def test_publish_conflicts_show_message_and_audit(  # noqa: PLR0917
    client: Any, login: Any, progression: Any, problem: Any, code: str, text: str
) -> None:
    login()
    _stub_event(progression, _run())
    progression.route("POST", f"{EVENTS}/{KEY}/runs/{RUN}/publish").respond(
        409, json=problem(409, code)
    )
    response = client.post(
        f"/events/{KEY}/runs/{RUN}/publish/",
        {"body_sha256": SHA, "confirm": "on", "reason": "публикуем", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert text in response.content.decode()
    entry = AuditEntry.objects.get(action="events.publish_run")
    assert (entry.outcome, entry.error_code) == (AuditEntry.Outcome.FAILED, code)


def test_stop_run(client: Any, login: Any, progression: Any) -> None:
    login()
    route = progression.route("POST", f"{EVENTS}/{KEY}/runs/{RUN}/stop").respond(
        200, json=_run(status="stopped", stopped_at=NOW)
    )
    response = client.post(
        f"/events/{KEY}/runs/{RUN}/stop/", {"reason": "ошибка в тексте", "idempotency_key": _key()}
    )
    assert response["Location"] == f"/events/{KEY}/"
    assert json.loads(route.calls.last.request.content) == {"reason": "ошибка в тексте"}
    assert AuditEntry.objects.get(action="events.stop_run").target_id == RUN


def test_stop_requires_reason(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression, _run(status="active"))
    response = client.post(f"/events/{KEY}/runs/{RUN}/stop/", {"idempotency_key": _key()})
    assert response.status_code == 400
    assert not AuditEntry.objects.exists()


def test_restart_is_new_run_with_new_period_on_finished_event(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression, _run(status="finished"))
    page = client.get(f"/events/{KEY}/").content.decode()
    assert "Новый запуск" in page
    assert "Статистика" in page


# ------------------------------------------------------------------------------ статистика


def test_stats_show_players_per_find(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", f"{EVENTS}/{KEY}/runs/{RUN}/stats").respond(
        200,
        json={
            "run_id": RUN,
            "players": 4,
            "completed": 1,
            "finds": [
                {"find_key": "first_leaf", "number": 1, "unlocked": 4},
                {"find_key": "duel_trail", "number": 2, "unlocked": 2},
            ],
        },
    )
    page = client.get(f"/events/{KEY}/runs/{RUN}/stats/").content.decode()
    assert "first_leaf" in page
    assert "100%" in page
    assert "50%" in page
