"""«Сборники», YAML-редактор кураторского сборника (D14): создание, новая версия, ошибки."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

CID = uuid.uuid4()
ITEMS = [uuid.uuid4() for _ in range(5)]
API = f"/admin/v1/collections/{CID}"
EDIT = f"/collections/{CID}/edit/"
NOW = "2026-10-16T10:00:00Z"
CURATOR = {"kind": "curator", "user_id": None, "nickname": None}


def _detail(version: int = 2, **kw: Any) -> dict[str, Any]:
    return {
        "collection_id": str(CID),
        "domain": "film",
        "kind": "curated",
        "status": "published",
        "visibility": "public",
        "title": "Классика кино",
        "description": "Пять фильмов",
        "author": CURATOR,
        "author_restricted": False,
        "featured": True,
        "featured_rank": 1,
        "hidden": None,
        "items_count": 5,
        "likes_count": 0,
        "plays_count": 0,
        "difficulty": None,
        "version": version,
        "published_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "default_mode_key": "film.collection.frame",
        "tags": ["classic"],
        "cover": None,
        "draft_revision": None,
        "items": [
            {
                "position": i,
                "media_item_id": str(item),
                "title": f"Фильм {i}",
                "year": 1970 + i,
                "available": True,
                "custom": {"emoji": "🎬🎞️🍿🎥📽️🎦"} if i == 0 else None,
            }
            for i, item in enumerate(ITEMS)
        ],
    } | kw


def _text(*, items: list[uuid.UUID] = ITEMS, emoji: str = "🎬🎞️🍿🎥📽️🎦") -> str:
    lines = [
        "domain: film",
        "title: Классика кино",
        "description: Пять фильмов",
        "tags: [classic]",
        "default_mode_key: film.collection.frame",
        "visibility: public",
        "items:",
        f"  - media_item_id: {items[0]}  # Фильм 0",
        f'    custom: {{emoji: "{emoji}"}}',
        *(f"  - {i}" for i in items[1:]),
    ]
    return "\n".join(lines) + "\n"


def _key() -> str:
    return str(uuid.uuid4())


def _body(**kw: Any) -> dict[str, Any]:
    return {"text": _text(), "reason": "кураторская подборка", "idempotency_key": _key()} | kw


# ---------------------------------------------------------------------------- создание


def test_new_editor_has_template_for_domain(client: Any, login: Any) -> None:
    login()
    html = client.get("/collections/new/", {"domain": "music"}).content.decode()
    assert "domain: music" in html
    assert "music.collection." in html


def test_create_sends_body_and_audits_without_texts(
    client: Any, login: Any, collections: Any
) -> None:
    login()
    key = _key()
    route = collections.route("POST", "/admin/v1/collections").respond(201, json=_detail(version=1))
    response = client.post("/collections/new/", _body(idempotency_key=key))
    assert response.status_code == 302
    assert response["Location"] == f"/collections/{CID}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    sent = json.loads(request.content)
    assert sent["domain"] == "film" and sent["reason"] == "кураторская подборка"
    assert sent["items"][0] == {
        "media_item_id": str(ITEMS[0]),
        "custom": {"emoji": "🎬🎞️🍿🎥📽️🎦"},
    }
    assert sent["items"][1:] == [{"media_item_id": str(i)} for i in ITEMS[1:]]
    entry = AuditEntry.objects.get(action="collections.create_curated")
    assert entry.after == {
        "collection_id": str(CID),
        "version": 1,
        "status": "published",
        "items_count": 5,
    }
    # В аудит — версии и статусы, не тексты (название, описание, эмодзи).
    dumped = json.dumps([entry.before, entry.after], ensure_ascii=False)
    assert "Классика" not in dumped and "🎬" not in dumped


def test_publish_errors_get_lines_and_audit(
    client: Any, login: Any, collections: Any, problem: Any
) -> None:
    login()
    body = problem(422, "collections.publish_invalid") | {
        "details": {
            "errors": [
                {"path": "items[3]", "code": "item_unavailable"},
                {"path": "items[0].custom.emoji", "code": "emoji_invalid"},
                {"path": "tags[0]", "code": "tag_unknown"},
            ]
        }
    }
    collections.route("POST", "/admin/v1/collections").respond(422, json=body)
    response = client.post("/collections/new/", _body())
    html = response.content.decode()
    assert "Сборник не прошёл проверку" in html
    assert "<td>12</td><td><code>items[3]</code></td>" in html  # строка единицы в тексте
    assert "единицы нет в каталоге или она заблокирована" in html
    assert "<td>9</td><td><code>items[0].custom.emoji</code></td>" in html
    assert "неизвестный тег" in html
    assert str(ITEMS[3]) in html  # текст остался в форме
    entry = AuditEntry.objects.get(action="collections.create_curated")
    assert entry.error_code == "collections.publish_invalid"


def test_service_validation_errors_are_mapped_to_lines(
    client: Any, login: Any, collections: Any, problem: Any
) -> None:
    login()
    body = problem(422, "request.validation_failed") | {
        "errors": [
            {"loc": ["body", "default_mode_key"], "msg": "String should match pattern"},
        ]
    }
    collections.route("POST", "/admin/v1/collections").respond(422, json=body)
    html = client.post("/collections/new/", _body()).content.decode()
    assert "<td>5</td><td><code>default_mode_key</code></td>" in html


def test_bad_yaml_is_not_sent(client: Any, login: Any, collections: Any) -> None:
    login()
    route = collections.route("POST", "/admin/v1/collections").respond(201, json=_detail())
    bad = _text().replace(f"  - {ITEMS[2]}", "  - not-a-uuid")
    response = client.post("/collections/new/", _body(text=bad))
    assert response.status_code == 400
    assert "строка 11" in response.content.decode()
    assert not route.called
    assert not AuditEntry.objects.exists()


def test_reason_is_required(client: Any, login: Any, collections: Any) -> None:
    login()
    route = collections.route("POST", "/admin/v1/collections").respond(201, json=_detail())
    response = client.post("/collections/new/", _body(reason=""))
    assert response.status_code == 400
    assert not route.called


def test_editor_needs_curate_perm(
    client: Any, login: Any, collections: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    route = collections.route("POST", "/admin/v1/collections").respond(201, json=_detail())
    assert client.get("/collections/new/").status_code == 403
    assert client.post("/collections/new/", _body()).status_code == 403
    assert client.get(EDIT).status_code == 403
    assert not route.called


# ---------------------------------------------------------------------------- новая версия


def test_edit_shows_yaml_with_base_version(client: Any, login: Any, collections: Any) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    html = client.get(EDIT).content.decode()
    assert f"  - {ITEMS[1]}  # Фильм 1 (1971)" in html
    assert "custom: {emoji: &quot;🎬🎞️🍿🎥📽️🎦&quot;}" in html
    assert 'name="base_version" value="2"' in html


def test_update_sends_base_version_without_domain(
    client: Any, login: Any, collections: Any
) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("PUT", API).respond(200, json=_detail(version=3))
    response = client.post(EDIT, _body(base_version="2"))
    assert response.status_code == 302
    sent = json.loads(route.calls.last.request.content)
    assert "domain" not in sent
    assert (sent["base_version"], sent["title"]) == (2, "Классика кино")
    entry = AuditEntry.objects.get(action="collections.update_curated")
    assert (entry.before, entry.after) == (
        {"version": 2},
        {"version": 3, "status": "published", "items_count": 5},
    )


def test_version_conflict_keeps_text_and_shows_diff(
    client: Any, login: Any, collections: Any, problem: Any
) -> None:
    login()
    newer = _detail(version=3, title="Классика кино (новая)")
    collections.route("GET", API).respond(200, json=newer)
    conflict = problem(409, "collections.version_conflict") | {"details": {"current_version": 3}}
    collections.route("PUT", API).respond(409, json=conflict)
    html = client.post(EDIT, _body(base_version="2")).content.decode()
    assert "Сборник изменили, пока вы его правили" in html
    assert "текущая версия v3" in html
    assert "-title: Классика кино (новая)" in html and "+title: Классика кино" in html
    assert str(ITEMS[4]) in html  # текст остался в форме
    entry = AuditEntry.objects.get(action="collections.update_curated")
    assert entry.error_code == "collections.version_conflict"


def test_domain_change_is_rejected_locally(client: Any, login: Any, collections: Any) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("PUT", API).respond(200, json=_detail(version=3))
    text = _text().replace("domain: film", "domain: music")
    response = client.post(EDIT, _body(text=text, base_version="2"))
    assert response.status_code == 400
    assert "домен сборника менять нельзя" in response.content.decode()
    assert not route.called


def test_ugc_collection_is_not_editable(client: Any, login: Any, collections: Any) -> None:
    login()
    ugc = _detail(kind="ugc", author={"kind": "user", "user_id": str(uuid.uuid4())})
    collections.route("GET", API).respond(200, json=ugc)
    route = collections.route("PUT", API).respond(200, json=_detail())
    response = client.post(EDIT, _body(base_version="2"), follow=True)
    assert "правит только автор" in response.content.decode()
    assert not route.called
