"""«Режимы», этап 3a: «Запросить ассеты», запросы режима, статус профилей и предупреждения.

game — запросы ассетов (``/admin/v1/mode-configs/{key}/asset-requests``), media-pipeline —
статус профилей (``GET /admin/v1/profiles``). Заглушки проверяют запросы и ответы по контракту.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

KEY = "film.random.description"
BASE = f"/admin/v1/mode-configs/{KEY}"
NOW = "2026-10-16T09:00:00Z"
HASH = "a" * 64
REVEAL_HASH = "b" * 64
BODY: dict[str, Any] = {
    "key": KEY,
    "domain": "film",
    "clue": {"type": "description", "params": {"levels": 4}},
    "session": {"kind": "solo", "rounds": 5},
}


def _draft(*, warnings: list[dict[str, Any]] | None = None, ok: bool = True) -> dict[str, Any]:
    return {
        "body": BODY,
        "revision": 4,
        "validation": {
            "ok": ok,
            "errors": [],
            "warnings": warnings or [],
            "pool": {"playable_count": 0},
        },
        "updated_by": None,
        "updated_at": NOW,
    }


def _detail(draft: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "key": KEY,
        "domain": "film",
        "status": "draft",
        "published_version": None,
        "versions": [],
        "draft": draft,
    }


def _request(clue_type: str = "description", digest: str = HASH, **kw: Any) -> dict[str, Any]:
    return {
        "request_id": str(uuid.uuid4()),
        "mode_key": KEY,
        "draft_revision": 4,
        "clue_type": clue_type,
        "params_hash": digest,
        "status": "sent",
        "actor_id": str(uuid.uuid4()),
        "reason": "новый режим",
        "requested_at": NOW,
    } | kw


def _profile(clue_type: str = "description", digest: str = HASH, **kw: Any) -> dict[str, Any]:
    return {
        "clue_type": clue_type,
        "profile_key": None,
        "params_hash": digest,
        "origin": "dynamic",
        "status": "backfilling",
        "sets": {"ready": 12, "pending": 30, "failed": 1},
        "domains": ["film"],
        "error_code": None,
    } | kw


def _key() -> str:
    return str(uuid.uuid4())


MISSING = [
    {"code": "assets.profile_missing", "path": "clue.params"},
    {"code": "assets.profile_missing", "path": "clue.reveal.params"},
]


def test_detail_shows_profile_missing_warnings_and_request_button(
    client: Any, login: Any, game: Any
) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(_draft(warnings=MISSING)))
    game.route("GET", f"{BASE}/asset-requests").respond(200, json=[])
    page = client.get(f"/game/modes/{KEY}/").content.decode()
    assert "assets.profile_missing в <code>clue.params</code>" in page
    assert "<code>clue.reveal.params</code>" in page
    assert "Под параметры черновика ассетов нет" in page
    assert f'action="/game/modes/{KEY}/asset-requests/"' in page
    assert 'name="draft_revision" value="4"' in page
    assert "Запросов ассетов не было" in page


def test_editor_validation_shows_warning_path(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("POST", "/admin/v1/mode-configs/validate").respond(
        200,
        json={
            "ok": True,
            "errors": [],
            "warnings": [MISSING[1]],
            "pool": {"playable_count": 0},
        },
    )
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {"action": "validate", "fmt": "json", "text": json.dumps(BODY), "idempotency_key": _key()},
    )
    page = response.content.decode()
    assert "<code>clue.reveal.params</code>" in page
    assert "Запросить ассеты" in page  # пояснение к предупреждению


def test_request_assets_sends_revision_reason_key_and_audits(
    client: Any, login: Any, game: Any
) -> None:
    login()
    key = _key()
    created = _request(status="pending")
    route = game.route("POST", f"{BASE}/asset-requests").respond(201, json=created)
    response = client.post(
        f"/game/modes/{KEY}/asset-requests/",
        {"draft_revision": "4", "reason": "новый режим описаний", "idempotency_key": key},
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"draft_revision": 4, "reason": "новый режим описаний"}
    entry = AuditEntry.objects.get(action="game.request_assets")
    assert (entry.target_id, entry.error_code, entry.idempotency_key) == (KEY, "", key)
    assert entry.before == {"draft_revision": 4}
    assert entry.after == {
        "request_id": created["request_id"],
        "clue_type": "description",
        "params_hash": HASH,
        "status": "pending",
    }


def test_request_assets_already_available_is_explained_and_audited(
    client: Any, login: Any, game: Any, media: Any, problem: Any
) -> None:
    login()
    game.route("POST", f"{BASE}/asset-requests").respond(
        409, json=problem(409, "game.assets_already_available")
    )
    game.route("GET", BASE).respond(200, json=_detail(_draft()))
    game.route("GET", f"{BASE}/asset-requests").respond(200, json=[])
    response = client.post(
        f"/game/modes/{KEY}/asset-requests/",
        {"draft_revision": "4", "reason": "ещё раз", "idempotency_key": _key()},
        follow=True,
    )
    assert "Ассеты под параметры загадки черновика уже есть" in response.content.decode()
    entry = AuditEntry.objects.get(action="game.request_assets")
    assert entry.error_code == "game.assets_already_available"
    assert entry.after is None


def test_request_assets_without_reason_is_not_sent(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(_draft()))
    game.route("GET", f"{BASE}/asset-requests").respond(200, json=[])
    route = game.route("POST", f"{BASE}/asset-requests").respond(201, json=_request())
    response = client.post(
        f"/game/modes/{KEY}/asset-requests/", {"draft_revision": "4", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert not route.called
    assert not AuditEntry.objects.exists()


def test_panel_shows_requests_with_profile_status_from_media(
    client: Any, login: Any, game: Any, media: Any
) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(_draft()))
    game.route("GET", f"{BASE}/asset-requests").respond(
        200,
        json=[
            _request(),
            _request("frame_progressive", REVEAL_HASH),
            _request(digest="c" * 64),  # профиль ещё не заведён
        ],
    )
    profiles = media.route("GET", "/admin/v1/profiles").respond(
        200,
        json=[
            _profile(),
            _profile(
                "frame_progressive",
                REVEAL_HASH,
                status="rejected",
                error_code="profile.limit_reached",
            ),
        ],
    )
    page = client.get(f"/game/modes/{KEY}/").content.decode()
    assert "<strong>backfilling</strong> (dynamic)" in page
    assert "12 / 30 / 1" in page
    assert "<code>profile.limit_reached</code>" in page
    assert "ещё не заведён" in page
    # Один запрос к media на тип загадки, параметры — по контракту.
    asked = sorted(dict(c.request.url.params)["clue_type"] for c in profiles.calls)
    assert asked == ["description", "frame_progressive"]
    assert all(c.request.headers["Authorization"].startswith("Bearer ") for c in profiles.calls)


def test_panel_survives_media_outage(client: Any, login: Any, game: Any, media: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(_draft()))
    game.route("GET", f"{BASE}/asset-requests").respond(200, json=[_request()])
    media.route("GET", "/admin/v1/profiles").respond(503, json={"status": 503})
    response = client.get(f"/game/modes/{KEY}/")
    assert response.status_code == 200
    page = response.content.decode()
    assert "Статус профилей (media-pipeline)" in page
    assert "новый режим" in page  # запрос показан и без статуса профиля


def test_moderator_cannot_request_assets(
    client: Any, login: Any, game: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    game.route("GET", BASE).respond(200, json=_detail(_draft(warnings=MISSING)))
    game.route("GET", f"{BASE}/asset-requests").respond(200, json=[])
    route = game.route("POST", f"{BASE}/asset-requests").respond(201, json=_request())
    page = client.get(f"/game/modes/{KEY}/").content.decode()
    assert "/asset-requests/" not in page
    response = client.post(
        f"/game/modes/{KEY}/asset-requests/",
        {"draft_revision": "4", "reason": "хочу", "idempotency_key": _key()},
    )
    assert response.status_code == 403
    assert not route.called
    assert not AuditEntry.objects.exists()
