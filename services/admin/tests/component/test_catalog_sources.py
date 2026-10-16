"""«Каталог», этап 3a: загрузка исходников новых видов с происхождением и порядок кадров."""

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
SOURCE = uuid7()
ITEM_JSON = {
    "id": str(ITEM),
    "domain": "film",
    "kind": "film",
    "title": "Сталкер",
    "original_title": None,
    "subtitle": None,
    "year": 1979,
    "status": "active",
    "blocked_reason_code": None,
    "version": 1,
    "aliases": [],
    "persons": [],
    "genres": [],
    "external_refs": {},
    "metrics": {},
    "source_media": [],
}


def _uploaded(kind: str = "frame", position: int = 3, origin: str = "upload") -> dict[str, Any]:
    return {
        "source_media_id": str(SOURCE),
        "media_item_id": str(ITEM),
        "kind": kind,
        "sha256": "0" * 64,
        "bytes": 5,
        "mime": "image/jpeg",
        "scheduled_asset_set_ids": [],
        "position": position,
        "origin": origin,
    }


def _source_out(position: int) -> dict[str, Any]:
    return {
        "source_media_id": str(SOURCE),
        "media_item_id": str(ITEM),
        "kind": "frame",
        "origin": "upload",
        "position": position,
        "status": "ready",
        "scheduled_asset_set_ids": [str(uuid7())],
    }


def _key() -> str:
    return str(uuid.uuid4())


def _form(**kw: Any) -> dict[str, Any]:
    return {
        "kind": "frame",
        "position": "3",
        "origin": "upload",
        "source_url": "https://example.org/stalker/frame-3",
        "license_note": "собственный кадр, CC BY 4.0",
        "reason": "кадр для последовательности",
        "idempotency_key": _key(),
        "file": SimpleUploadedFile("frame-3.jpg", b"\xff\xd8jpg", content_type="image/jpeg"),
    } | kw


def _listed(source: uuid.UUID = SOURCE, position: int = 3, kind: str = "frame") -> dict[str, Any]:
    return {
        "source_media_id": str(source),
        "kind": kind,
        "position": position,
        "origin": "upload",
        "status": "ready",
        "created_at": "2026-10-16T10:00:00Z",
    }


def _open(catalog: Any, media: Any, sources: list[dict[str, Any]] | None = None) -> None:
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    media.route("GET", "/admin/v1/asset-sets").respond(200, json=[])
    media.route("GET", "/admin/v1/sources").respond(200, json=sources or [])


def test_item_page_offers_new_kinds_and_reorder(
    client: Any, login: Any, catalog: Any, media: Any
) -> None:
    login()
    _open(catalog, media)
    page = client.get(f"/catalog/items/{ITEM}/").content.decode()
    for kind in ("soundtrack", "audio_clip", "frame"):
        assert f'value="{kind}"' in page
    assert 'name="position"' in page and 'name="license_note"' in page
    assert f'href="/catalog/items/{ITEM}/clue-texts/"' in page


@pytest.mark.parametrize("kind", ["soundtrack", "audio_clip", "frame"])
def test_upload_sends_kind_position_and_provenance(
    client: Any, login: Any, catalog: Any, media: Any, kind: str
) -> None:
    login()
    _open(catalog, media)
    route = media.route("POST", "/admin/v1/sources").respond(201, json=_uploaded(kind))
    response = client.post(f"/catalog/items/{ITEM}/sources/", _form(kind=kind), follow=True)
    body = route.calls.last.request.content
    for name, value in (
        ("kind", kind),
        ("position", "3"),
        ("origin", "upload"),
        ("source_url", "https://example.org/stalker/frame-3"),
        ("license_note", "собственный кадр, CC BY 4.0"),
    ):
        assert f'name="{name}"\r\n\r\n{value}\r\n'.encode() in body
    assert str(SOURCE) in response.content.decode()  # id исходника — для смены порядка
    entry = AuditEntry.objects.get(action="media.upload_source")
    assert entry.after is not None
    assert (entry.after["kind"], entry.after["position"], entry.after["origin"]) == (
        kind,
        3,
        "upload",
    )


def test_empty_provenance_is_not_sent_in_ci(client: Any, login: Any, media: Any) -> None:
    login()
    route = media.route("POST", "/admin/v1/sources").respond(201, json=_uploaded(origin="upload"))
    client.post(f"/catalog/items/{ITEM}/sources/", _form(origin="", source_url="", license_note=""))
    body = route.calls.last.request.content
    assert b'name="origin"' not in body and b'name="source_url"' not in body


def test_provenance_required_outside_local_ci(
    client: Any, login: Any, catalog: Any, media: Any, settings: Any
) -> None:
    settings.GV_ENVIRONMENT = "prod"
    login()
    _open(catalog, media)
    route = media.route("POST", "/admin/v1/sources").respond(201, json=_uploaded())
    response = client.post(
        f"/catalog/items/{ITEM}/sources/", _form(origin="", source_url="", license_note="")
    )
    assert response.status_code == 400
    assert "без происхождения исходник не примут" in response.content.decode()
    assert not route.called


def test_provenance_refusal_from_media_is_explained_and_audited(
    client: Any, login: Any, catalog: Any, media: Any, problem: Any
) -> None:
    login()
    _open(catalog, media)
    media.route("POST", "/admin/v1/sources").respond(
        422, json=problem(422, "source.provenance_required")
    )
    response = client.post(f"/catalog/items/{ITEM}/sources/", _form(), follow=True)
    assert "Укажите происхождение исходника" in response.content.decode()
    assert AuditEntry.objects.get().error_code == "source.provenance_required"


def test_bad_position_and_url_are_form_errors(
    client: Any, login: Any, catalog: Any, media: Any
) -> None:
    login()
    _open(catalog, media)
    route = media.route("POST", "/admin/v1/sources").respond(201, json=_uploaded())
    response = client.post(
        f"/catalog/items/{ITEM}/sources/", _form(position="101", source_url="javascript:alert(1)")
    )
    assert response.status_code == 400
    assert not route.called


def test_sources_table_has_row_forms_with_current_position(
    client: Any, login: Any, catalog: Any, media: Any
) -> None:
    login()
    other = uuid7()
    route_sources = [_listed(position=2), _listed(other, position=5)]
    _open(catalog, media, route_sources)
    page = client.get(f"/catalog/items/{ITEM}/").content.decode()
    assert page.count(f'action="/catalog/items/{ITEM}/sources/position/"') == 2
    assert f'name="source_media_id" value="{SOURCE}"' in page
    assert f'name="source_media_id" value="{other}"' in page
    assert 'name="position" value="5"' in page  # текущий порядок — в поле
    assert 'id="src1_position"' in page and 'id="src2_position"' in page
    assert "Исходник (source_media_id)" not in page  # id руками больше не вводится


def test_sources_list_error_does_not_break_page(
    client: Any, login: Any, catalog: Any, media: Any, problem: Any
) -> None:
    login()
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    media.route("GET", "/admin/v1/asset-sets").respond(200, json=[])
    media.route("GET", "/admin/v1/sources").respond(403, json=problem(403, "auth.forbidden"))
    response = client.get(f"/catalog/items/{ITEM}/")
    assert response.status_code == 200
    assert "Недостаточно прав" in response.content.decode()


def test_reorder_frame_sends_position_and_audits(client: Any, login: Any, media: Any) -> None:
    login(perms=("admin.access", "media.upload_source"), roles=("content",))
    media.route("GET", "/admin/v1/sources").respond(200, json=[_listed(position=3)])
    key = _key()
    route = media.route("PUT", f"/admin/v1/sources/{SOURCE}/position").respond(
        200, json=_source_out(1)
    )
    response = client.post(
        f"/catalog/items/{ITEM}/sources/position/",
        {
            "source_media_id": str(SOURCE),
            "position": "1",
            "reason": "самый сложный кадр",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"position": 1, "reason": "самый сложный кадр"}
    entry = AuditEntry.objects.get(action="media.set_source_position")
    assert (entry.target_type, entry.target_id) == ("source_media", str(SOURCE))
    assert entry.before == {"media_item_id": str(ITEM), "position": 3}
    assert entry.after == {"position": 1, "kind": "frame", "media_item_id": str(ITEM)}


def test_reorder_refusal_is_audited(client: Any, login: Any, media: Any, problem: Any) -> None:
    login()
    media.route("GET", "/admin/v1/sources").respond(200, json=[])
    media.route("PUT", f"/admin/v1/sources/{SOURCE}/position").respond(
        404, json=problem(404, "source.not_found")
    )
    client.post(
        f"/catalog/items/{ITEM}/sources/position/",
        {
            "source_media_id": str(SOURCE),
            "position": "2",
            "reason": "порядок",
            "idempotency_key": _key(),
        },
    )
    assert (
        AuditEntry.objects.get(action="media.set_source_position").error_code == "source.not_found"
    )


def test_without_upload_perm_forms_are_hidden_and_forbidden(
    client: Any, login: Any, catalog: Any, media: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    _open(catalog, media, [_listed()])
    route = media.route("PUT", f"/admin/v1/sources/{SOURCE}/position").respond(
        200, json=_source_out(1)
    )
    page = client.get(f"/catalog/items/{ITEM}/").content.decode()
    assert "/sources/position/" not in page and f"/catalog/items/{ITEM}/sources/" not in page
    assert str(SOURCE) in page  # таблица исходников видна и без права загрузки
    response = client.post(
        f"/catalog/items/{ITEM}/sources/position/",
        {
            "source_media_id": str(SOURCE),
            "position": "1",
            "reason": "xxx",
            "idempotency_key": _key(),
        },
    )
    assert response.status_code == 403
    assert not route.called
    assert not AuditEntry.objects.exists()
