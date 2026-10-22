"""«Матчи» (этап 4a, план этапа 4 §10): активные комнаты, принудительное завершение, итог.

Заглушка match проверяет и ответы (по contracts/openapi/match.json), и запросы админки:
JSON-тело по схеме ``AbortRoomRequest`` и ``Idempotency-Key`` у завершения.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

CODE = "K7MQ2ZXA"
HOST = uuid.uuid4()
MATCH_ID = uuid.uuid4()
P1, P2 = uuid.uuid4(), uuid.uuid4()
ITEM = uuid.uuid4()
NOW = "2026-10-22T10:00:00Z"
ROOMS = "/admin/v1/rooms"
MODE = {
    "key": "music.duel.classic",
    "version": 3,
    "domain": "music",
    "kind": "duel",
    "title_key": "modes.music.duel.classic.title",
    "clue_type": "audio_intro",
    "answer_format": "classic",
    "ranked": False,
}


def _room(**kw: Any) -> dict[str, Any]:
    return {
        "code": CODE,
        "mode": MODE,
        "visibility": "private",
        "phase": "level_open",
        "host_id": str(HOST),
        "players": 2,
        "created_at": NOW,
        "match_id": str(MATCH_ID),
        "owner_epoch": 4,
    } | kw


def _result(**kw: Any) -> dict[str, Any]:
    return {
        "match_id": str(MATCH_ID),
        "room_code": CODE,
        "mode": MODE,
        "started_at": NOW,
        "finished_at": NOW,
        "finish": "aborted",
        "players": [
            {
                "user_id": str(P2),
                "nickname": "second_one",
                "slot": 1,
                "place": 2,
                "score": 6,
                "outcome": "loss",
                "finish": "completed",
            },
            {
                "user_id": str(P1),
                "nickname": "first_one",
                "slot": 0,
                "place": 1,
                "score": 12,
                "outcome": "win",
                "finish": "completed",
            },
        ],
        "rounds": [
            {
                "index": 0,
                "media_item_id": str(ITEM),
                "results": [
                    {"user_id": str(P1), "level": 0, "partial": False, "score": 12},
                    {"user_id": str(P2), "level": None, "partial": False, "score": 0},
                ],
            }
        ],
    } | kw


def _key() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------------------ доступ


def test_section_requires_match_manage(client: Any, login: Any, moderator_perms: Any) -> None:
    login(perms=moderator_perms, roles=("moderator",))  # moderator: match.spectate без manage
    assert client.get("/matches/").status_code == 403
    assert client.get(f"/matches/{MATCH_ID}/").status_code == 403
    response = client.post(
        f"/matches/rooms/{CODE}/abort/", {"reason": "тест прав", "idempotency_key": _key()}
    )
    assert response.status_code == 403
    assert 'href="/matches/"' not in client.get("/").content.decode()
    assert not AuditEntry.objects.exists()


def test_menu_shows_matches_with_perm(client: Any, login: Any) -> None:
    login()
    assert 'href="/matches/"' in client.get("/").content.decode()


def test_invalid_room_code_is_404_without_service_call(client: Any, login: Any) -> None:
    login()
    # 0/O/1/I в коде комнаты запрещены — маршрут не совпадает, в match ничего не уходит.
    assert client.get("/matches/rooms/K7MQ2ZX0/abort/").status_code == 404


# ------------------------------------------------------------------------------ комнаты


def test_rooms_list_with_phase_filter_cursor_and_audit(client: Any, login: Any, match: Any) -> None:
    login()
    route = match.route("GET", ROOMS).respond(
        200,
        json={
            "items": [_room(), _room(code="ABCDEFGH", match_id=None, owner_epoch=None)],
            "next_cursor": "c2",
        },
    )
    response = client.get("/matches/", {"phase": "level_open"})
    page = response.content.decode()
    assert response.status_code == 200
    assert dict(route.calls.last.request.url.params) == {"phase": "level_open"}
    assert f'href="/matches/rooms/{CODE}/abort/"' in page
    assert f'href="/matches/{MATCH_ID}/"' in page
    assert "music.duel.classic v3" in page
    assert "нет владельца" in page  # owner_epoch = null — комната без владельца
    assert 'href="?phase=level_open&amp;cursor=c2"' in page
    entry = AuditEntry.objects.get(action="match.view_rooms")
    assert (entry.service, entry.target_type) == ("match", "room_list")
    assert entry.after == {"phase": "level_open", "rooms": [CODE, "ABCDEFGH"]}


def test_rooms_list_survives_match_unavailable(
    client: Any, login: Any, match: Any, problem: Any
) -> None:
    login()
    match.route("GET", ROOMS).respond(403, json=problem(403, "auth.forbidden", "Нет права"))
    response = client.get("/matches/")
    assert response.status_code == 200
    assert "Активных комнат нет" not in response.content.decode()
    assert not AuditEntry.objects.exists()


def test_rooms_list_tolerates_broken_rows(client: Any, login: Any, match: Any) -> None:
    login()
    match.route("GET", ROOMS).respond_off_contract(
        200, json={"items": [{"code": "../../x", "match_id": "not-a-uuid"}, "junk"]}
    )
    page = client.get("/matches/").content.decode()
    assert "/abort/" not in page.split("<table>", 1)[1]


def test_abort_form_requires_reason(client: Any, login: Any, match: Any) -> None:
    login()
    page = client.get(f"/matches/rooms/{CODE}/abort/").content.decode()
    assert 'name="idempotency_key"' in page
    response = client.post(
        f"/matches/rooms/{CODE}/abort/", {"reason": "", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert not AuditEntry.objects.exists()


def test_abort_sends_reason_with_idempotency_key_and_audits(
    client: Any, login: Any, match: Any
) -> None:
    login()
    key = _key()
    route = match.route("POST", f"{ROOMS}/{CODE}/abort").respond(
        202, json={"accepted": True, "version": 17}
    )
    response = client.post(
        f"/matches/rooms/{CODE}/abort/", {"reason": "оскорбления в чате", "idempotency_key": key}
    )
    assert response.status_code == 302
    assert response["Location"] == "/matches/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"reason": "оскорбления в чате"}
    entry = AuditEntry.objects.get(action="match.abort_room")
    assert (entry.service, entry.target_type, entry.target_id) == ("match", "room", CODE)
    assert entry.reason == "оскорбления в чате"
    assert entry.idempotency_key == key
    assert entry.outcome == AuditEntry.Outcome.SUCCEEDED
    assert entry.after == {"accepted": True, "version": 17}


@pytest.mark.parametrize(
    ("status", "code"), [(404, "match.room_not_found"), (409, "match.room_closed")]
)
def test_abort_refusal_is_shown_and_audited(
    client: Any, login: Any, match: Any, problem: Any, *, status: int, code: str
) -> None:
    login()
    match.route("POST", f"{ROOMS}/{CODE}/abort").respond(
        status, json=problem(status, code, "Комната уже закрыта")
    )
    match.route("GET", ROOMS).respond(200, json={"items": []})
    response = client.post(
        f"/matches/rooms/{CODE}/abort/",
        {"reason": "зависла", "idempotency_key": _key()},
        follow=True,
    )
    assert "Комната уже закрыта" in response.content.decode()
    entry = AuditEntry.objects.get(action="match.abort_room")
    assert entry.outcome == AuditEntry.Outcome.FAILED
    assert entry.error_code == code


# ------------------------------------------------------------------------------ итог


def test_match_result_shows_places_rounds_and_audits(client: Any, login: Any, match: Any) -> None:
    login()
    match.route("GET", f"/admin/v1/matches/{MATCH_ID}").respond(200, json=_result())
    page = client.get(f"/matches/{MATCH_ID}/").content.decode()
    assert page.index("first_one") < page.index("second_one")  # по месту, а не по порядку ответа
    assert "принудительно" in page
    assert f'href="/catalog/items/{ITEM}/"' in page
    assert "second_one: 0 (не угадал)" in page
    entry = AuditEntry.objects.get(action="match.view_result")
    assert (entry.target_type, entry.target_id) == ("match", str(MATCH_ID))


def test_match_result_not_found(client: Any, login: Any, match: Any, problem: Any) -> None:
    login()
    match.route("GET", f"/admin/v1/matches/{MATCH_ID}").respond(
        404, json=problem(404, "match.match_not_found")
    )
    response = client.get(f"/matches/{MATCH_ID}/")
    assert response.status_code == 404
    assert "нет" in response.content.decode()
    assert not AuditEntry.objects.exists()


def test_match_lookup_redirects_to_result(client: Any, login: Any) -> None:
    login()
    response = client.get("/matches/lookup/", {"match_id": str(MATCH_ID)})
    assert response["Location"] == f"/matches/{MATCH_ID}/"
    assert client.get("/matches/lookup/", {"match_id": "nope"})["Location"] == "/matches/"
