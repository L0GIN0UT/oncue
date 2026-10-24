"""«UGC-изображения» (этап 5b, поток S4): очередь, карточка с превью, решения.

Заглушка media_pipeline проверяет ответы и запросы админки по contracts/openapi/media_pipeline.json.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

NOW = "2026-10-23T10:00:00Z"
ASSET = str(uuid.uuid4())
BASE = "/admin/v1/ugc-images"
SIGNED = "https://s3.test/ugc/a.webp?X-Signature=SECRETSIG"
ONLY = ("admin.access", "media.ugc.moderate")
SETTINGS = {
    "purposes": [
        {
            "purpose": "avatar",
            "max_bytes": 300000,
            "formats": ["webp"],
            "min_side_px": 64,
            "max_side_px": 512,
        }
    ],
    "reasons": [
        {"code": "nsfw", "title": "Недопустимое содержимое"},
        {"code": "copyright", "title": "Чужие права"},
    ],
}


def _key() -> str:
    return str(uuid.uuid4())


def _image(**patch: Any) -> dict[str, Any]:
    return {
        "asset_id": ASSET,
        "owner_user_id": str(uuid.uuid4()),
        "purpose": "avatar",
        "status": "pending",
        "reason_code": None,
        "width": 256,
        "height": 256,
        "bytes": 12345,
        "submitted_at": NOW,
        "decided_at": None,
        "moderator_id": None,
        "version": 1,
    } | patch


def _stub(media: Any, *rows: dict[str, Any], next_cursor: str | None = None) -> Any:
    media.route("GET", f"{BASE}/settings").respond(200, json=SETTINGS)
    media.route("GET", f"{BASE}/{ASSET}/preview").respond(
        200, json={"url": SIGNED, "expires_at": NOW}
    )
    return media.route("GET", BASE).respond(
        200, json={"items": list(rows) or [_image()], "next_cursor": next_cursor}
    )


# ------------------------------------------------------------------------------ доступ


def test_hidden_and_forbidden_without_perm(client: Any, login: Any) -> None:
    login(perms=("admin.access", "progression.items.manage"), roles=("moderator",))
    assert 'href="/ugc/"' not in client.get("/").content.decode()
    assert client.get("/ugc/").status_code == 403
    assert client.get(f"/ugc/{ASSET}/").status_code == 403
    body = {"reason": "причина", "idempotency_key": _key()}
    assert client.post(f"/ugc/{ASSET}/approve/", body).status_code == 403
    assert client.post(f"/ugc/{ASSET}/reject/", body | {"reason_code": "nsfw"}).status_code == 403
    assert not AuditEntry.objects.exists()


def test_perm_opens_only_ugc(client: Any, login: Any, media: Any) -> None:
    login(perms=ONLY, roles=("moderator",))
    _stub(media)
    assert 'href="/ugc/"' in client.get("/").content.decode()
    assert client.get("/ugc/").status_code == 200
    assert client.get("/shop/").status_code == 403
    assert client.get("/broadcasts/").status_code == 403


# ------------------------------------------------------------------------------ очередь


def test_queue_defaults_to_pending_with_filters(client: Any, login: Any, media: Any) -> None:
    login()
    route = _stub(media, next_cursor="nxt")
    page = client.get("/ugc/").content.decode()
    assert "avatar" in page and "cursor=nxt" in page
    assert route.calls.last.request.url.params["status"] == "pending"
    client.get("/ugc/?status=approved&purpose=avatar&cursor=c1")
    params = route.calls.last.request.url.params
    assert (params["status"], params["purpose"], params["cursor"]) == ("approved", "avatar", "c1")


def test_queue_unknown_status_falls_back_to_pending(client: Any, login: Any, media: Any) -> None:
    login()
    route = _stub(media)
    client.get("/ugc/?status=hacked")
    assert route.calls.last.request.url.params["status"] == "pending"


@pytest.mark.parametrize(
    ("status", "code"), [(501, "feature.not_implemented"), (403, "auth.forbidden")]
)
def test_queue_survives_service_errors(  # noqa: PLR0917
    client: Any, login: Any, media: Any, problem: Any, status: int, code: str
) -> None:
    login()
    media.route("GET", f"{BASE}/settings").respond(status, json=problem(status, code))
    media.route("GET", BASE).respond(status, json=problem(status, code))
    assert client.get("/ugc/").status_code == 200


# ------------------------------------------------------------------------------ карточка


def test_card_requests_signed_preview_each_time_and_does_not_store_it(
    client: Any, login: Any, media: Any
) -> None:
    login()
    _stub(media)
    response = client.get(f"/ugc/{ASSET}/")
    page = response.content.decode()
    assert f'src="{SIGNED}"'.replace("&", "&amp;") in page or SIGNED in page
    assert "no-store" in response["Cache-Control"]
    preview = media.route("GET", f"{BASE}/{ASSET}/preview")
    client.get(f"/ugc/{ASSET}/")
    assert preview.call_count == 2
    # ссылка не сохраняется ни в сессии, ни в аудите, ни в куках
    assert "SECRETSIG" not in json.dumps(dict(client.session.items()), default=str)
    assert "SECRETSIG" not in str(client.cookies)
    assert {e.action for e in AuditEntry.objects.all()} == {"ugc.view_preview"}


def test_card_of_missing_image_is_404(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media, _image(asset_id=str(uuid.uuid4())))
    assert client.get(f"/ugc/{ASSET}/").status_code == 404


def test_card_pending_has_approve_and_reject_with_reasons(
    client: Any, login: Any, media: Any
) -> None:
    login()
    _stub(media)
    page = client.get(f"/ugc/{ASSET}/").content.decode()
    assert 'value="Одобрить"' in page
    assert 'value="Отклонить"' in page
    assert '<option value="nsfw">' in page and "Чужие права" in page


def test_card_approved_offers_take_down(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media, _image(status="approved"))
    page = client.get(f"/ugc/{ASSET}/?status=approved").content.decode()
    assert 'value="Снять с публикации"' in page
    assert 'value="Одобрить"' not in page


# ------------------------------------------------------------------------------ решения


def test_approve_sends_reason_key_and_audits(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media)
    route = media.route("POST", f"{BASE}/{ASSET}/approve").respond(
        200, json=_image(status="approved", version=2)
    )
    key = _key()
    response = client.post(
        f"/ugc/{ASSET}/approve/", {"reason": "всё в порядке", "idempotency_key": key}
    )
    assert response["Location"] == "/ugc/?status=pending"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"reason": "всё в порядке"}
    entry = AuditEntry.objects.get(action="ugc.approve")
    assert (entry.service, entry.target_id, entry.reason) == (
        "media_pipeline",
        ASSET,
        "всё в порядке",
    )
    assert entry.after == {"status": "approved", "reason_code": None, "version": 2}
    assert "SECRETSIG" not in json.dumps(entry.after)


def test_same_form_resubmitted_has_stable_key(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media)
    route = media.route("POST", f"{BASE}/{ASSET}/approve").respond(200, json=_image())
    page = client.get(f"/ugc/{ASSET}/").content.decode()
    key = page.split('name="idempotency_key" value="')[1].split('"')[0]
    data = {"reason": "всё в порядке", "idempotency_key": key}
    client.post(f"/ugc/{ASSET}/approve/", data)
    client.post(f"/ugc/{ASSET}/approve/", data)
    assert {c.request.headers["Idempotency-Key"] for c in route.calls} == {key}


def test_reason_required(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media)
    route = media.route("POST", f"{BASE}/{ASSET}/approve").respond(200, json=_image())
    response = client.post(f"/ugc/{ASSET}/approve/", {"reason": "", "idempotency_key": _key()})
    assert response.status_code == 400
    reject = client.post(
        f"/ugc/{ASSET}/reject/", {"reason": "", "reason_code": "nsfw", "idempotency_key": _key()}
    )
    assert reject.status_code == 400
    assert route.call_count == 0
    # аудит решений пуст (просмотр карточки при показе ошибок формы - не решение)
    assert not AuditEntry.objects.exclude(action="ugc.view_preview").exists()


def test_reject_uses_code_from_settings(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media)
    route = media.route("POST", f"{BASE}/{ASSET}/reject").respond(
        200, json=_image(status="rejected", reason_code="nsfw", version=2)
    )
    unknown = client.post(
        f"/ugc/{ASSET}/reject/",
        {"reason": "нарушение", "reason_code": "made_up", "idempotency_key": _key()},
    )
    assert unknown.status_code == 400
    assert route.call_count == 0
    key = _key()
    ok = client.post(
        f"/ugc/{ASSET}/reject/",
        {"reason": "нарушение", "reason_code": "nsfw", "idempotency_key": key},
    )
    assert ok.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"reason_code": "nsfw", "reason": "нарушение"}
    entry = AuditEntry.objects.get(action="ugc.reject")
    assert entry.after is not None and entry.after["reason_code"] == "nsfw"


def test_take_down_approved_is_reject_from_approved(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media, _image(status="approved"))
    route = media.route("POST", f"{BASE}/{ASSET}/reject").respond(
        200, json=_image(status="rejected", reason_code="copyright", version=3)
    )
    response = client.post(
        f"/ugc/{ASSET}/reject/?status=approved",
        {"reason": "жалоба правообладателя", "reason_code": "copyright", "idempotency_key": _key()},
    )
    assert response["Location"] == "/ugc/?status=approved"
    assert route.call_count == 1
    assert AuditEntry.objects.get(action="ugc.reject").before == {"status": "approved"}


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (409, "ugc.not_pending", "уже обработано"),
        (404, "ugc.not_found", "не найдено"),
        (422, "ugc.reason_unknown", "не описан"),
    ],
)
def test_service_errors_are_messages(  # noqa: PLR0917
    client: Any, login: Any, media: Any, problem: Any, status: int, code: str, text: str
) -> None:
    login()
    _stub(media)
    media.route("POST", f"{BASE}/{ASSET}/approve").respond(status, json=problem(status, code))
    response = client.post(
        f"/ugc/{ASSET}/approve/",
        {"reason": "всё в порядке", "idempotency_key": _key()},
        follow=True,
    )
    assert response.status_code == 200
    assert text in response.content.decode()
    entry = AuditEntry.objects.get(action="ugc.approve")
    assert (entry.outcome, entry.error_code) == (AuditEntry.Outcome.FAILED, code)


def test_service_503_is_message(client: Any, login: Any, media: Any, problem: Any) -> None:
    login()
    _stub(media)
    media.route("POST", f"{BASE}/{ASSET}/approve").respond_off_contract(
        503, json=problem(503, "dependency.unavailable")
    )
    response = client.post(
        f"/ugc/{ASSET}/approve/",
        {"reason": "всё в порядке", "idempotency_key": _key()},
        follow=True,
    )
    assert response.status_code == 200
    assert "временно недоступен" in response.content.decode()


# ------------------------------------------------------------------------------ ревью S4


def test_card_distinguishes_service_errors_from_not_found(
    client: Any, login: Any, media: Any, problem: Any
) -> None:
    login()
    media.route("GET", f"{BASE}/settings").respond(200, json=SETTINGS)
    route = media.route("GET", BASE).respond(403, json=problem(403, "auth.forbidden"))
    assert client.get(f"/ugc/{ASSET}/").status_code == 403
    route.respond_off_contract(503, json=problem(503, "dependency.unavailable"))
    assert client.get(f"/ugc/{ASSET}/").status_code == 503


def test_card_view_is_audited_without_signed_link(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media)
    client.get(f"/ugc/{ASSET}/")
    entry = AuditEntry.objects.get(action="ugc.view_preview")
    assert (entry.service, entry.target_id) == ("media_pipeline", ASSET)
    assert "SECRETSIG" not in json.dumps([entry.after, entry.before, entry.reason])


def test_decision_audit_takes_status_from_real_row(client: Any, login: Any, media: Any) -> None:
    login()
    _stub(media, _image(status="approved"))
    media.route("POST", f"{BASE}/{ASSET}/approve").respond(200, json=_image())
    # ?status= подменён: в очереди pending, а реальная строка — approved
    client.post(
        f"/ugc/{ASSET}/approve/?status=pending",
        {"reason": "всё в порядке", "idempotency_key": _key()},
    )
    assert AuditEntry.objects.get(action="ugc.approve").before == {"status": "approved"}


def test_pagination_links_are_url_encoded(client: Any, login: Any, media: Any) -> None:
    login()
    media.route("GET", f"{BASE}/settings").respond(200, json=SETTINGS)
    media.route("GET", BASE).respond_off_contract(
        200, json={"items": [_image()], "next_cursor": "c&x=1"}
    )
    page = client.get("/ugc/?purpose=a%26b").content.decode()
    assert "cursor=c%26x%3D1" in page
    assert "purpose=a%26b" in page
