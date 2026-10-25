"""«События»: редактор находок формой (рядом с YAML-режимом, одно и то же тело определения)."""

from __future__ import annotations

import copy
import html
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from admin_service.apps.events import finds_form as ff
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

SHA = "b" * 64
EVENTS = "/admin/v1/events"
KEY = "new_year_2026"
_EXAMPLE = Path(__file__).resolve().parents[4] / "contracts" / "progression" / "examples"
BODY = json.loads((_EXAMPLE / "event_definition.v1.json").read_text("utf-8"))


def _event(
    body: dict[str, Any] = BODY, map_url: str | None = "https://s3/map.png"
) -> dict[str, Any]:
    return {
        "key": KEY,
        "body": body,
        "body_sha256": SHA,
        "map_url": map_url,
        "runs": [],
        "updated_at": "2026-12-01T10:00:00Z",
    }


def _post(body: dict[str, Any] = BODY, **patch: str) -> dict[str, str]:
    header = ff.header_from_body(body)
    rows = ff.rows_from_body(body)
    data = {f"h_{k}": v for k, v in header.items()} | {
        "mode": "form",
        "find_count": str(len(rows)),
        "base": json.dumps(body, ensure_ascii=False),
        "reason": "правка формой",
        "idempotency_key": str(uuid.uuid4()),
        "op": "save",
    }
    for i, row in enumerate(rows):
        for name in ff.ROW_FIELDS:
            data[f"f{i}_{name}"] = str(row[name])
        if row["final"]:
            data["final_index"] = str(i)
    return data | patch


def _stub_event(progression: Any, body: dict[str, Any] = BODY) -> Any:
    route = progression.route("GET", f"{EVENTS}/{KEY}")
    if body is BODY:
        return route.respond(200, json=_event(body))
    return route.respond_off_contract(200, json=_event(body))  # чужие поля вне схемы


def test_form_is_default_and_renders_fields_and_map_picker(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression)
    page = client.get(f"/events/{KEY}/edit/").content.decode()
    assert 'data-testid="finds-form"' in page
    assert 'name="f0_key" value="first_leaf"' in page
    assert "data-map-pick" in page
    assert "https://s3/map.png" in page
    assert "admin_events/map_picker.js" in page
    assert "<script>" not in page  # без inline-скриптов


def test_yaml_mode_still_available(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    page = client.get(f"/events/{KEY}/edit/?mode=yaml").content.decode()
    assert 'name="text"' in page
    assert 'value="to_form"' in page


def test_map_picker_script_is_served_as_static(client: Any, login: Any) -> None:
    login()
    response = client.get("/static/admin_events/map_picker.js")
    assert response.status_code == 200


def test_form_save_sends_body_to_update_and_audits(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression)
    route = progression.route("PUT", f"{EVENTS}/{KEY}").respond(200, json=_event())
    data = _post(f0_title="Новый лист", f0_x_pct="33.3")
    response = client.post(f"/events/{KEY}/edit/", data)
    assert response["Location"] == f"/events/{KEY}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == data["idempotency_key"]
    sent = json.loads(request.content)
    expected = copy.deepcopy(BODY)
    expected["finds"][0].update({"title": "Новый лист", "x_pct": 33.3})
    assert sent["body"] == expected
    assert sent["reason"] == "правка формой"
    entry = AuditEntry.objects.get(action="events.update")
    assert entry.before == {"body_sha256": SHA}
    assert "Новый лист" not in json.dumps(entry.after, ensure_ascii=False)


def test_form_create_uses_key(client: Any, login: Any, progression: Any) -> None:
    login()
    route = progression.route("POST", EVENTS).respond(201, json=_event())
    response = client.post("/events/new/", _post(key=KEY))
    assert response["Location"] == f"/events/{KEY}/"
    sent = json.loads(route.calls.last.request.content)
    assert (sent["key"], sent["body"]) == (KEY, BODY)


def test_new_event_page_opens_in_form_mode(client: Any, login: Any) -> None:
    login()
    page = client.get("/events/new/").content.decode()
    assert 'data-testid="finds-form"' in page
    assert 'name="key"' in page


def test_form_validation_blocks_call_and_shows_field_error(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression)
    response = client.post(f"/events/{KEY}/edit/", _post(f0_x_pct="150"))
    assert response.status_code == 400
    assert "число 0-100" in response.content.decode()
    assert not AuditEntry.objects.exists()


def test_form_requires_reason(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    response = client.post(f"/events/{KEY}/edit/", _post(reason=""))
    assert response.status_code == 400
    assert not AuditEntry.objects.exists()


def test_server_error_is_shown_at_field(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_event(progression)
    error = problem(422, "progression.event_definition_invalid") | {
        "details": {"errors": [{"path": "finds[1].trigger.params.slot", "reason": "нет слота"}]}
    }
    progression.route("PUT", f"{EVENTS}/{KEY}").respond(422, json=error)
    response = client.post(f"/events/{KEY}/edit/", _post())
    page = response.content.decode()
    assert response.status_code == 400
    assert page.count("нет слота") >= 2  # в таблице и у поля
    field_block = page.split('data-testid="find-1"')[1].split("</fieldset>")[0]
    assert "нет слота" in field_block
    assert AuditEntry.objects.get(action="events.update").error_code


def test_ops_change_list_without_mutation(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    page = client.post(f"/events/{KEY}/edit/", _post(op="add")).content.decode()
    assert 'name="find_count" value="5"' in page
    page = client.post(f"/events/{KEY}/edit/", _post(op="del:0")).content.decode()
    assert 'name="find_count" value="3"' in page
    page = client.post(f"/events/{KEY}/edit/", _post(op="up:1")).content.decode()
    assert page.index('value="duel_trail"') < page.index('value="first_leaf"')
    assert not AuditEntry.objects.exists()


def test_switching_modes_keeps_body_and_unknown_fields(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    body = copy.deepcopy(BODY)
    body["future_top"] = {"a": 1}
    body["finds"][0]["future_find"] = "keep"
    _stub_event(progression, body)
    # тело с чужими полями вне схемы: проверку запроса по контракту обходим намеренно
    route = progression.route("PUT", f"{EVENTS}/{KEY}").mock(
        return_value=httpx.Response(200, json=_event(body))
    )
    # форма -> YAML: правка попадает в текст вместе с чужими полями
    to_yaml = client.post(
        f"/events/{KEY}/edit/", _post(body, f0_title="Из формы", op="to_yaml")
    ).content.decode()
    assert "Из формы" in to_yaml
    assert "future_find" in to_yaml
    assert "future_top" in to_yaml
    text = re.search(r'<textarea[^>]*name="text"[^>]*>(.*?)</textarea>', to_yaml, re.S)
    assert text
    yaml_text = html.unescape(text.group(1)).lstrip("\n")
    # YAML -> форма: правка в YAML видна в полях формы, чужие поля уходят в base
    to_form = client.post(
        f"/events/{KEY}/edit/",
        {
            "fmt": "yaml",
            "text": yaml_text.replace("Из формы", "Из YAML"),
            "op": "to_form",
            "idempotency_key": str(uuid.uuid4()),
            "reason": "",
        },
    ).content.decode()
    assert 'name="f0_title" value="Из YAML"' in to_form
    found = re.search(r'name="base" value="([^"]*)"', to_form)
    assert found
    base = json.loads(html.unescape(found.group(1)))
    assert base["finds"][0]["future_find"] == "keep"
    # сохранение из формы отправляет всё тело целиком
    client.post(f"/events/{KEY}/edit/", _post(base, f0_title="Из YAML"))
    sent = json.loads(route.calls.last.request.content)["body"]
    assert sent["finds"][0]["future_find"] == "keep"
    assert sent["future_top"] == {"a": 1}
    assert sent["finds"][0]["title"] == "Из YAML"


def test_to_form_with_broken_yaml_stays_in_yaml(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    response = client.post(
        f"/events/{KEY}/edit/",
        {"fmt": "yaml", "text": "a: [", "op": "to_form", "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert 'name="text"' in response.content.decode()


def test_form_save_needs_manage_permission(client: Any, login: Any) -> None:
    login(perms=("admin.access", "progression.items.manage"), roles=("moderator",))
    assert client.post(f"/events/{KEY}/edit/", _post()).status_code == 403


def test_edit_page_carries_seen_hash_in_both_modes(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression)
    for url in (f"/events/{KEY}/edit/", f"/events/{KEY}/edit/?mode=yaml"):
        page = client.get(url).content.decode()
        assert f'name="expected_sha" value="{SHA}"' in page


def test_form_save_sends_expected_hash_seen_by_admin(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression)
    route = progression.route("PUT", f"{EVENTS}/{KEY}").respond(200, json=_event())
    seen = "a" * 64  # хеш на момент открытия формы, не текущий
    client.post(f"/events/{KEY}/edit/", _post(expected_sha=seen))
    assert json.loads(route.calls.last.request.content)["expected_body_sha256"] == seen


def test_form_save_without_hash_keeps_old_behaviour(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_event(progression)
    route = progression.route("PUT", f"{EVENTS}/{KEY}").respond(200, json=_event())
    client.post(f"/events/{KEY}/edit/", _post())
    assert "expected_body_sha256" not in json.loads(route.calls.last.request.content)


def test_form_conflict_shows_message_and_keeps_entered_data(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_event(progression)
    conflict = problem(409, "progression.event_definition_changed")
    progression.route("PUT", f"{EVENTS}/{KEY}").respond(409, json=conflict)
    seen = "a" * 64
    response = client.post(f"/events/{KEY}/edit/", _post(f0_title="Мой текст", expected_sha=seen))
    page = response.content.decode()
    assert response.status_code == 400
    assert "Определение изменили, откройте заново" in page
    assert 'name="f0_title" value="Мой текст"' in page  # введённое не потеряно
    assert f'name="expected_sha" value="{seen}"' in page
    assert AuditEntry.objects.get(action="events.update").error_code == (
        "progression.event_definition_changed"
    )


def test_yaml_save_conflict_shows_message(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_event(progression)
    conflict = problem(409, "progression.event_definition_changed")
    route = progression.route("PUT", f"{EVENTS}/{KEY}").respond(409, json=conflict)
    seen = "a" * 64
    response = client.post(
        f"/events/{KEY}/edit/",
        {
            "fmt": "json",
            "text": json.dumps(BODY),
            "reason": "yaml правка",
            "idempotency_key": str(uuid.uuid4()),
            "expected_sha": seen,
        },
    )
    assert json.loads(route.calls.last.request.content)["expected_body_sha256"] == seen
    assert response.status_code == 400
    assert "Определение изменили, откройте заново" in response.content.decode()


def test_modes_carry_hash_through_switches(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_event(progression)
    seen = "a" * 64
    to_yaml = client.post(f"/events/{KEY}/edit/", _post(op="to_yaml", expected_sha=seen))
    assert f'name="expected_sha" value="{seen}"' in to_yaml.content.decode()
    to_form = client.post(
        f"/events/{KEY}/edit/",
        {
            "fmt": "json",
            "text": json.dumps(BODY),
            "op": "to_form",
            "idempotency_key": str(uuid.uuid4()),
            "expected_sha": seen,
        },
    )
    assert f'name="expected_sha" value="{seen}"' in to_form.content.decode()
