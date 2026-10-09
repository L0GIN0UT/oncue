"""Раздел «Каталог»: действия идут в admin-API с reason и Idempotency-Key и пишут аудит."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry
from django.core.files.uploadedfile import SimpleUploadedFile

from gv_core.ids import uuid7

pytestmark = [pytest.mark.component, pytest.mark.django_db]

ITEM = uuid7()
ITEM_JSON = {
    "id": str(ITEM),
    "title": "Интерстеллар",
    "original_title": "Interstellar",
    "year": 2014,
    "domain": "film",
    "kind": "film",
    "status": "active",
    "aliases": [
        {"text": "Интерстеллар", "normalized": "интерстеллар", "lang": "ru", "kind": "title"}
    ],
}


def _key() -> str:
    return str(uuid.uuid4())


def test_list_renders_items_and_passes_filters(client: Any, login: Any, catalog: Any) -> None:
    login()
    route = catalog.route("GET", "/admin/v1/items").respond(
        200,
        json={"items": [ITEM_JSON, {"id": "not-a-uuid", "title": "Битый"}], "next_cursor": "c2"},
    )
    response = client.get("/catalog/", {"q": "инте", "domain": "film", "status": ""})
    assert response.status_code == 200
    body = response.content.decode()
    assert "Интерстеллар" in body and f"/catalog/items/{ITEM}/" in body
    assert "Битый" in body  # некорректный id не роняет страницу
    assert dict(route.calls.last.request.url.params) == {"q": "инте", "domain": "film"}


def test_detail_survives_media_pipeline_outage(
    client: Any, login: Any, catalog: Any, media: Any
) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    media.route("GET", "/internal/v1/asset-sets").respond(503, json={"status": 503})
    response = client.get(f"/catalog/items/{ITEM}/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Интерстеллар" in body
    assert "Интерстеллар | ru | title" in body  # текущие алиасы в форме


def test_block_calls_admin_api_and_writes_audit(client: Any, login: Any, catalog: Any) -> None:
    user_id = login(perms=("admin.access", "catalog.block_item"), roles=("moderator",))
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    block = catalog.route("POST", f"/admin/v1/items/{ITEM}/block").respond(
        200, json={**ITEM_JSON, "status": "blocked"}
    )
    key = _key()

    response = client.post(
        f"/catalog/items/{ITEM}/block/", {"reason": "правообладатель", "idempotency_key": key}
    )

    assert response.status_code == 302
    request = block.calls.last.request
    assert json.loads(request.content) == {"reason": "правообладатель"}
    assert request.headers["Idempotency-Key"] == key
    assert request.headers["Authorization"].startswith("Bearer ")
    assert uuid.UUID(request.headers["X-Request-Id"])
    entry = AuditEntry.objects.get()
    assert entry.action == "catalog.block_item"
    assert entry.actor.identity_user_id == user_id
    assert entry.actor_identity_id == user_id
    assert entry.target_id == str(ITEM)
    assert entry.reason == "правообладатель"
    assert entry.before == {"status": "active"}
    assert entry.after["status"] == "blocked"
    assert entry.outcome == "succeeded"
    assert entry.idempotency_key == key
    assert entry.request_id == request.headers["X-Request-Id"]


def test_unblock(client: Any, login: Any, catalog: Any) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(
        200, json={**ITEM_JSON, "status": "blocked"}
    )
    unblock = catalog.route("POST", f"/admin/v1/items/{ITEM}/unblock").respond(200, json=ITEM_JSON)
    client.post(f"/catalog/items/{ITEM}/unblock/", {"reason": "ошибка", "idempotency_key": _key()})
    assert unblock.called
    assert AuditEntry.objects.get().action == "catalog.unblock_item"


@pytest.mark.parametrize("reason", ["", "   ", "ok"])
def test_empty_reason_is_form_error_without_call(
    client: Any, login: Any, catalog: Any, media: Any, reason: str
) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    media.route("GET", "/internal/v1/asset-sets").respond(200, json=[])
    block = catalog.route("POST", f"/admin/v1/items/{ITEM}/block").respond(200, json={})
    response = client.post(
        f"/catalog/items/{ITEM}/block/", {"reason": reason, "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert not block.called
    assert not AuditEntry.objects.exists()


def test_missing_idempotency_key_is_form_error(
    client: Any, login: Any, catalog: Any, media: Any
) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    media.route("GET", "/internal/v1/asset-sets").respond(200, json=[])
    block = catalog.route("POST", f"/admin/v1/items/{ITEM}/block").respond(200, json={})
    response = client.post(f"/catalog/items/{ITEM}/block/", {"reason": "причина"})
    assert response.status_code == 400
    assert not block.called


def test_service_problem_is_shown_and_audited_as_failed(
    client: Any, login: Any, catalog: Any, media: Any
) -> None:
    login()
    media.route("GET", "/internal/v1/asset-sets").respond(200, json=[])
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    catalog.route("POST", f"/admin/v1/items/{ITEM}/block").respond(
        403,
        json={"type": "about:blank", "title": "Forbidden", "status": 403, "code": "auth.forbidden"},
        headers={"content-type": "application/problem+json"},
    )
    response = client.post(
        f"/catalog/items/{ITEM}/block/",
        {"reason": "причина", "idempotency_key": _key()},
        follow=True,
    )
    assert "Недостаточно прав" in response.content.decode()
    entry = AuditEntry.objects.get()
    assert entry.outcome == "failed"
    assert entry.error_code == "auth.forbidden"


def test_moderator_without_perm_cannot_block(client: Any, login: Any, catalog: Any) -> None:
    login(perms=("admin.access", "catalog.edit_aliases"), roles=("moderator",))
    block = catalog.route("POST", f"/admin/v1/items/{ITEM}/block").respond(200, json={})
    response = client.post(
        f"/catalog/items/{ITEM}/block/", {"reason": "причина", "idempotency_key": _key()}
    )
    assert response.status_code == 403
    assert not block.called


def test_replace_aliases(client: Any, login: Any, catalog: Any) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    put = catalog.route("PUT", f"/admin/v1/items/{ITEM}/aliases").respond(200, json=ITEM_JSON)
    client.post(
        f"/catalog/items/{ITEM}/aliases/",
        {
            "aliases": "Интерстеллар | ru | title\nInterstellar | en |\n\nИнтерстелар",
            "reason": "опечатки",
            "idempotency_key": _key(),
        },
    )
    assert json.loads(put.calls.last.request.content) == {
        "aliases": [
            {"text": "Интерстеллар", "lang": "ru", "kind": "title"},
            {"text": "Interstellar", "lang": "en", "kind": "alt"},
            {"text": "Интерстелар", "lang": None, "kind": "alt"},
        ],
        "reason": "опечатки",
    }
    assert AuditEntry.objects.get().before == {"aliases": ITEM_JSON["aliases"]}


def test_bad_alias_kind_is_form_error(client: Any, login: Any, catalog: Any, media: Any) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    media.route("GET", "/internal/v1/asset-sets").respond(200, json=[])
    response = client.post(
        f"/catalog/items/{ITEM}/aliases/",
        {"aliases": "X | ru | nickname", "reason": "причина", "idempotency_key": _key()},
    )
    assert response.status_code == 400


def test_tmdb_import_redirects_to_status(client: Any, login: Any, catalog: Any) -> None:
    login()
    import_id = uuid7()
    post = catalog.route("POST", "/admin/v1/imports").respond(
        202, json={"import_id": str(import_id)}
    )
    catalog.route("GET", f"/admin/v1/imports/{import_id}").respond(
        200, json={"import_id": str(import_id), "status": "running"}
    )
    response = client.post(
        "/catalog/imports/new/",
        {"source": "tmdb", "ref": "157336", "reason": "демо", "idempotency_key": _key()},
    )
    assert response.status_code == 302
    assert json.loads(post.calls.last.request.content) == {
        "source": "tmdb",
        "ref": "157336",
        "reason": "демо",
    }
    status = client.get(response["Location"])
    assert "running" in status.content.decode()
    assert AuditEntry.objects.get().action == "catalog.import"


def test_file_import_requires_file_id(client: Any, login: Any, catalog: Any) -> None:
    login()
    post = catalog.route("POST", "/admin/v1/imports").respond(202, json={})
    response = client.post(
        "/catalog/imports/new/", {"source": "file", "reason": "сиды", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert not post.called


def test_file_import_sends_file_id(client: Any, login: Any, catalog: Any) -> None:
    login()
    post = catalog.route("POST", "/admin/v1/imports").respond(202, json={"import_id": str(uuid7())})
    client.post(
        "/catalog/imports/new/",
        {
            "source": "file",
            "file_id": "seeds/films.jsonl",
            "reason": "сиды",
            "idempotency_key": _key(),
        },
    )
    assert json.loads(post.calls.last.request.content) == {
        "source": "file",
        "file_id": "seeds/films.jsonl",
        "reason": "сиды",
    }


def test_upload_source_is_multipart(client: Any, login: Any, media: Any) -> None:
    login()
    upload = media.route("POST", "/admin/v1/sources").respond(201, json={"source_media_ref": "x"})
    key = _key()
    client.post(
        f"/catalog/items/{ITEM}/sources/",
        {
            "kind": "audio",
            "reason": "аудио правообладателя",
            "idempotency_key": key,
            "file": SimpleUploadedFile("tone.m4a", b"\x00\x01audio", content_type="audio/mp4"),
        },
    )
    request = upload.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert str(ITEM).encode() in body and b'name="kind"' in body and b"\x00\x01audio" in body
    entry = AuditEntry.objects.get()
    assert entry.action == "media.upload_source"
    assert entry.after == {"kind": "audio", "file_name": "tone.m4a", "bytes": 7}


def test_regenerate_asset_set(client: Any, login: Any, media: Any) -> None:
    login()
    asset_set = uuid7()
    regen = media.route("POST", f"/admin/v1/asset-sets/{asset_set}/regenerate").respond(
        202, json={}
    )
    response = client.post(
        f"/catalog/items/{ITEM}/asset-sets/{asset_set}/regenerate/",
        {"reason": "размытие слабое", "idempotency_key": _key()},
    )
    assert response.status_code == 302
    assert json.loads(regen.calls.last.request.content) == {"reason": "размытие слабое"}
    assert AuditEntry.objects.get().target_id == str(asset_set)


def test_service_unavailable_is_reported(client: Any, login: Any, catalog: Any) -> None:
    import httpx

    login()
    catalog.route("GET", "/admin/v1/items").mock(side_effect=httpx.ConnectError("down"))
    response = client.get("/catalog/")
    assert response.status_code == 200
    assert "Сервис catalog недоступен" in response.content.decode()
