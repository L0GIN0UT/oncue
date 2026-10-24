"""«Магазин» (этап 5b, поток S4): предметы, картинки, паки, партии промокодов.

Заглушка progression проверяет ответы по contracts/openapi/progression.json и запросы админки
(JSON/multipart по схемам, ``Idempotency-Key`` у мутаций).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
import pytest
import structlog
from admin_service.audit.models import AuditEntry
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = [pytest.mark.component, pytest.mark.django_db]

NOW = "2026-10-23T10:00:00Z"
ITEM = str(uuid.uuid4())
PACK = str(uuid.uuid4())
BATCH = str(uuid.uuid4())
SHA = "a" * 64
ITEMS = "/admin/v1/items"
PROMO = "/admin/v1/promo-codes"
CODES = ["ABCD-1111-ZZZZ", "QWER-2222-YYYY"]
ITEMS_ONLY = ("admin.access", "progression.items.manage")
PROMO_ONLY = ("admin.access", "progression.promo.manage")
SHOP = {
    "slots": [{"key": "avatar", "title": "Аватар"}, {"key": "frame", "title": "Рамка"}],
    "rarities": [{"key": "common", "title": "Обычный"}, {"key": "rare", "title": "Редкий"}],
    "media": {"static_max_bytes": 1000, "animated_max_bytes": 2000, "max_side_px": 512},
}


def _key() -> str:
    return str(uuid.uuid4())


def _item(**patch: Any) -> dict[str, Any]:
    return {
        "id": ITEM,
        "key": "gold_frame",
        "slot": "frame",
        "rarity": "rare",
        "title": "Золотая рамка",
        "media_kind": "static",
        "event_key": None,
        "level_required": None,
        "price": 100,
        "active": False,
        "asset_url": None,
        "poster_url": None,
        "owners": 0,
        "created_at": NOW,
        "updated_at": NOW,
    } | patch


def _stub_config(progression: Any, shop: dict[str, Any] | None = None) -> None:
    card = {
        "version": 1,
        "status": "published",
        "created_at": NOW,
        "created_by": str(uuid.uuid4()),
        "body_sha256": SHA,
        "published_at": NOW,
    }
    progression.route("GET", "/admin/v1/configs").respond(200, json={"items": [card]})
    progression.route("GET", "/admin/v1/configs/1").respond(
        200, json={"card": card, "config": {"shop": shop or SHOP}}
    )


def _stub_items(progression: Any, *rows: dict[str, Any]) -> Any:
    return progression.route("GET", ITEMS).respond(
        200, json={"items": list(rows) or [_item()], "next_cursor": None}
    )


def _pack(**patch: Any) -> dict[str, Any]:
    return {
        "id": PACK,
        "key": "starter",
        "title": "Стартовый",
        "style": "bright",
        "item_ids": [ITEM],
        "verses_amount": 10,
        "price_kopecks": 9900,
        "active": False,
        "created_at": NOW,
        "updated_at": NOW,
    } | patch


def _batch(**patch: Any) -> dict[str, Any]:
    return {
        "id": BATCH,
        "label": "лето",
        "reward": {"kind": "verses", "verses_amount": 50, "item_id": None},
        "codes_total": 2,
        "activations": 0,
        "max_uses": 1,
        "per_user_once": True,
        "expires_at": None,
        "status": "active",
        "created_at": NOW,
    } | patch


def _item_form(**patch: Any) -> dict[str, Any]:
    return {
        "slot": "frame",
        "rarity": "rare",
        "title": "Золотая рамка",
        "media_kind": "static",
        "level_required": "",
        "price": "100",
        "reason": "новый предмет",
        "idempotency_key": _key(),
    } | patch


def _promo_form(**patch: Any) -> dict[str, Any]:
    return {
        "label": "лето",
        "reward_kind": "verses",
        "verses_amount": "50",
        "count": "2",
        "max_uses": "1",
        "per_user_once": "on",
        "reason": "акция",
        "idempotency_key": _key(),
    } | patch


# ------------------------------------------------------------------------------ доступ


def test_section_hidden_and_forbidden_without_perms(client: Any, login: Any) -> None:
    login(perms=("admin.access", "progression.config.manage"), roles=("moderator",))
    page = client.get("/").content.decode()
    assert 'href="/shop/"' not in page
    assert 'href="/shop/promos/"' not in page
    for path in (
        "",
        "items/new/",
        f"items/{ITEM}/",
        "packs/",
        "packs/new/",
        "promos/",
        "promos/new/",
    ):
        assert client.get(f"/shop/{path}").status_code == 403
    assert client.post(f"/shop/promos/{BATCH}/revoke/", {}).status_code == 403
    assert not AuditEntry.objects.exists()


def test_items_perm_does_not_open_promos_and_vice_versa(client: Any, login: Any) -> None:
    login(perms=ITEMS_ONLY, roles=("moderator",))
    assert client.get("/shop/promos/").status_code == 403
    assert client.post("/shop/promos/new/", _promo_form()).status_code == 403
    assert 'href="/shop/"' in client.get("/").content.decode()
    client.logout()
    login(perms=PROMO_ONLY, roles=("moderator",))
    assert client.get("/shop/").status_code == 403
    assert client.post("/shop/items/new/", _item_form(key="x")).status_code == 403
    assert 'href="/shop/promos/"' in client.get("/").content.decode()


# ------------------------------------------------------------------------------ предметы


def test_item_list_filters_and_cursor(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    route = progression.route("GET", ITEMS).respond(
        200, json={"items": [_item()], "next_cursor": "abc"}
    )
    page = client.get("/shop/?slot=frame&active=1&cursor=c1").content.decode()
    assert "gold_frame" in page
    assert "cursor=abc" in page
    params = route.calls.last.request.url.params
    assert (params["slot"], params["active"], params["cursor"]) == ("frame", "true", "c1")


def test_item_list_survives_service_error(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_config(progression)
    progression.route("GET", ITEMS).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/shop/")
    assert response.status_code == 200
    assert "Недостаточно прав" in response.content.decode()


def test_create_item_uses_config_choices_and_idempotency_key(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_config(progression)
    page = client.get("/shop/items/new/").content.decode()
    assert '<option value="avatar">' in page  # слот и редкость - из shop конфига
    assert '<option value="rare">' in page
    route = progression.route("POST", ITEMS).respond(201, json=_item())
    _stub_items(progression)
    key = _key()
    response = client.post("/shop/items/new/", _item_form(key="gold_frame", idempotency_key=key))
    assert response["Location"] == f"/shop/items/{ITEM}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    body = json.loads(request.content)
    assert (body["key"], body["slot"], body["active"], body["level_required"]) == (
        "gold_frame",
        "frame",
        False,
        None,
    )
    entry = AuditEntry.objects.get(action="shop.create_item")
    assert (entry.service, entry.target_id, entry.reason) == (
        "progression",
        "gold_frame",
        "новый предмет",
    )
    assert entry.idempotency_key == key


def test_create_item_requires_reason(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    no_reason = client.post("/shop/items/new/", _item_form(key="a_b", reason=""))
    assert no_reason.status_code == 400
    assert not AuditEntry.objects.exists()


def test_post_does_not_fetch_config_service_validates_slot(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    config = progression.route("GET", "/admin/v1/configs").respond(200, json={"items": []})
    progression.route("POST", ITEMS).respond(
        422, json=problem(422, "progression.item_slot_unknown")
    )
    response = client.post("/shop/items/new/", _item_form(key="a_b", slot="unknown"))
    assert response.status_code == 400
    assert "Слот не описан" in response.content.decode()
    _stub_items(progression)
    progression.route("PUT", f"{ITEMS}/{ITEM}").respond(200, json=_item())
    client.post(f"/shop/items/{ITEM}/", _item_form())
    assert config.call_count == 0  # конфиг нужен только для показа формы


def test_create_item_falls_back_to_text_without_config(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("GET", "/admin/v1/configs").respond(403, json=problem(403, "auth.forbidden"))
    page = client.get("/shop/items/new/").content.decode()
    assert "Конфиг магазина недоступен" in page
    assert 'name="slot"' in page
    route = progression.route("POST", ITEMS).respond(201, json=_item())
    _stub_items(progression)
    response = client.post("/shop/items/new/", _item_form(key="gold_frame", slot="whatever"))
    assert response.status_code == 302  # значение проверит сервис
    assert json.loads(route.calls.last.request.content)["slot"] == "whatever"


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (409, "progression.item_key_taken", "уже есть"),
        (422, "progression.item_slot_unknown", "Слот не описан"),
        (404, "resource.not_found", "не найден"),
    ],
)
def test_create_item_service_errors_show_message_and_audit(  # noqa: PLR0917
    client: Any, login: Any, progression: Any, problem: Any, status: int, code: str, text: str
) -> None:
    login()
    _stub_config(progression)
    progression.route("POST", ITEMS).respond(status, json=problem(status, code))
    response = client.post("/shop/items/new/", _item_form(key="gold_frame"))
    assert response.status_code == 400
    assert text in response.content.decode()
    entry = AuditEntry.objects.get(action="shop.create_item")
    assert (entry.outcome, entry.error_code) == (AuditEntry.Outcome.FAILED, code)


def test_edit_item_key_is_immutable(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression)
    page = client.get(f"/shop/items/{ITEM}/").content.decode()
    assert "Золотая рамка" in page
    route = progression.route("PUT", f"{ITEMS}/{ITEM}").respond(200, json=_item(price=200))
    key = _key()
    response = client.post(
        f"/shop/items/{ITEM}/",
        _item_form(key="hacked_key", price="200", reason="цена", idempotency_key=key),
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content)["key"] == "gold_frame"  # из карточки, не из формы
    entry = AuditEntry.objects.get(action="shop.update_item")
    assert entry.before is not None and entry.before["price"] == 100


def test_edit_same_form_resubmitted_keeps_idempotency_key(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression)
    route = progression.route("PUT", f"{ITEMS}/{ITEM}").respond(200, json=_item())
    page = client.get(f"/shop/items/{ITEM}/").content.decode()
    shown = set(page.split('name="idempotency_key" value="')[1:])
    keys = {chunk.split('"', 1)[0] for chunk in shown}
    assert len(keys) == 2  # у формы картинки и формы параметров свои ключи
    key = sorted(keys)[0]
    data = _item_form(idempotency_key=key)
    client.post(f"/shop/items/{ITEM}/", data)
    client.post(f"/shop/items/{ITEM}/", data)
    sent = {c.request.headers["Idempotency-Key"] for c in route.calls}
    assert sent == {key}


def test_edit_unknown_item_is_404(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression, _item(id=str(uuid.uuid4())))
    assert client.get(f"/shop/items/{ITEM}/").status_code == 404


# ------------------------------------------------------------------------------ картинка


def _upload(size: int, name: str = "a.webp") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"x" * size, content_type="image/webp")


def test_media_upload_sends_multipart_and_audits_without_content(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression)
    updated = _item(asset_url="https://cdn.test/a.webp", poster_url="https://cdn.test/p.webp")
    route = progression.route("PUT", f"{ITEMS}/{ITEM}/media").respond(200, json=updated)
    key = _key()
    response = client.post(
        f"/shop/items/{ITEM}/media/",
        {"file": _upload(900), "reason": "картинка", "idempotency_key": key},
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert 'name="reason"\r\n\r\nкартинка'.encode() in request.content
    assert b'name="file"' in request.content
    entry = AuditEntry.objects.get(action="shop.upload_item_media")
    assert entry.after == {"bytes": 900, "content_type": "image/webp"}


def test_media_over_config_limit_is_rejected_before_service_call(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_config(progression)  # static_max_bytes = 1000
    _stub_items(progression)
    route = progression.route("PUT", f"{ITEMS}/{ITEM}/media").respond(200, json=_item())
    response = client.post(
        f"/shop/items/{ITEM}/media/",
        {"file": _upload(1001), "reason": "картинка", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert "больше допустимых 1000" in response.content.decode()
    assert route.call_count == 0
    assert not AuditEntry.objects.exists()


def test_animated_item_uses_animated_limit(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression, _item(media_kind="animated"))
    route = progression.route("PUT", f"{ITEMS}/{ITEM}/media").respond(200, json=_item())
    response = client.post(
        f"/shop/items/{ITEM}/media/",
        {"file": _upload(1500), "reason": "анимация", "idempotency_key": _key()},
    )
    assert response.status_code == 302
    assert route.call_count == 1


def test_media_hard_ceiling_applies_without_config(
    client: Any, login: Any, progression: Any, problem: Any, settings: Any
) -> None:
    login()
    settings.GV_SHOP_MEDIA_MAX_BYTES = 500
    progression.route("GET", "/admin/v1/configs").respond(403, json=problem(403, "auth.forbidden"))
    _stub_items(progression)
    route = progression.route("PUT", f"{ITEMS}/{ITEM}/media").respond(200, json=_item())
    response = client.post(
        f"/shop/items/{ITEM}/media/",
        {"file": _upload(600), "reason": "картинка", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert route.call_count == 0


def test_stream_over_hard_limit_is_cut_by_upload_handler(
    client: Any, login: Any, progression: Any, settings: Any
) -> None:
    login()
    settings.GV_SHOP_MEDIA_MAX_BYTES = 1024
    _stub_config(progression)
    _stub_items(progression)
    route = progression.route("PUT", f"{ITEMS}/{ITEM}/media").respond(200, json=_item())
    big = _upload(200_000)
    response = client.post(
        f"/shop/items/{ITEM}/media/",
        {"file": big, "reason": "картинка", "idempotency_key": _key()},
    )
    assert response.status_code == 400  # RequestDataTooBig из обработчика загрузки
    assert route.call_count == 0


def test_media_413_from_service_is_message(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression)
    progression.route("PUT", f"{ITEMS}/{ITEM}/media").respond(
        413, json=problem(413, "progression.item_media_too_large")
    )
    response = client.post(
        f"/shop/items/{ITEM}/media/",
        {"file": _upload(10), "reason": "картинка", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert "Файл больше допустимого" in response.content.decode()
    entry = AuditEntry.objects.get(action="shop.upload_item_media")
    assert entry.error_code == "progression.item_media_too_large"


def test_item_page_shows_asset_and_poster_preview(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_config(progression)
    _stub_items(
        progression,
        _item(
            media_kind="animated",
            asset_url="https://cdn.test/a.webp",
            poster_url="https://cdn.test/p.webp",
        ),
    )
    page = client.get(f"/shop/items/{ITEM}/").content.decode()
    assert 'src="https://cdn.test/a.webp"' in page
    assert 'src="https://cdn.test/p.webp"' in page
    assert "до 2000 байт" in page


@pytest.mark.parametrize(("status", "text"), [(429, "Слишком много запросов"), (503, "недоступен")])
def test_service_429_and_503_are_messages_not_500(  # noqa: PLR0917
    client: Any, login: Any, progression: Any, problem: Any, status: int, text: str
) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression)
    code = "rate.limited" if status == 429 else "dependency.unavailable"
    progression.route("PUT", f"{ITEMS}/{ITEM}").respond_off_contract(
        status, json=problem(status, code)
    )
    response = client.post(f"/shop/items/{ITEM}/", _item_form())
    assert response.status_code == 400
    assert text in response.content.decode()


# ------------------------------------------------------------------------------ паки


def test_pack_create_and_notice(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_items(progression)
    progression.route("GET", "/admin/v1/packs").respond(200, json={"items": [_pack()]})
    page = client.get("/shop/packs/").content.decode()
    assert "billing.enabled=false" in page
    assert "starter" in page
    route = progression.route("POST", "/admin/v1/packs").respond(201, json=_pack())
    key = _key()
    response = client.post(
        "/shop/packs/new/",
        {
            "key": "starter",
            "title": "Стартовый",
            "style": "bright",
            "item_ids": [ITEM],
            "verses_amount": "10",
            "price_kopecks": "9900",
            "reason": "новый пак",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content)["item_ids"] == [ITEM]
    assert AuditEntry.objects.filter(action="shop.create_pack").exists()


def test_pack_edit_key_immutable_and_409_message(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_items(progression)
    progression.route("GET", "/admin/v1/packs").respond(200, json={"items": [_pack()]})
    route = progression.route("PUT", f"/admin/v1/packs/{PACK}").respond(
        409, json=problem(409, "progression.pack_key_taken")
    )
    response = client.post(
        f"/shop/packs/{PACK}/",
        {
            "key": "other",
            "title": "Стартовый",
            "style": "bright",
            "item_ids": [ITEM],
            "verses_amount": "10",
            "price_kopecks": "9900",
            "reason": "правка",
            "idempotency_key": _key(),
        },
    )
    assert response.status_code == 400
    assert "Пак с таким ключом" in response.content.decode()
    assert json.loads(route.calls.last.request.content)["key"] == "starter"


def test_pack_needs_items_and_reason(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_items(progression)
    response = client.post(
        "/shop/packs/new/",
        {"key": "p", "title": "t", "style": "s", "price_kopecks": "1", "verses_amount": "0",
         "reason": "", "idempotency_key": _key()},
    )  # fmt: skip
    assert response.status_code == 400
    assert not AuditEntry.objects.exists()


# ------------------------------------------------------------------------------ промокоды


def test_promo_list_has_no_codes_and_revoke_link(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", PROMO).respond(
        200,
        json={
            "items": [_batch(), _batch(id=str(uuid.uuid4()), status="revoked")],
            "next_cursor": None,
        },
    )
    page = client.get("/shop/promos/").content.decode()
    assert "лето" in page
    assert page.count("Отозвать</a>") == 1


def test_promo_create_shows_codes_once_no_store_and_csv(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    _stub_items(progression)
    key = _key()
    route = progression.route("POST", PROMO).respond(
        201, json={"id": BATCH, "label": "лето", "codes": CODES}
    )
    response = client.post("/shop/promos/new/", _promo_form(idempotency_key=key))
    assert response.status_code == 200
    page = response.content.decode()
    assert all(code in page for code in CODES)
    assert "Скачать CSV" in page
    assert "code%0AABCD-1111-ZZZZ%0AQWER-2222-YYYY" in page
    assert "no-store" in response["Cache-Control"]
    assert response["Pragma"] == "no-cache"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    body = json.loads(request.content)
    assert body["reward"] == {"kind": "verses", "verses_amount": 50}
    assert body["count"] == 2


def test_promo_codes_never_stored(
    client: Any, login: Any, progression: Any, capsys: Any, caplog: Any
) -> None:
    login()
    _stub_items(progression)
    progression.route("POST", PROMO).respond(
        201, json={"id": BATCH, "label": "лето", "codes": CODES}
    )
    caplog.set_level(logging.INFO)  # DEBUG-шаблонизатор Django печатает контекст целиком
    with structlog.testing.capture_logs() as captured:
        client.post("/shop/promos/new/", _promo_form())
    haystacks = [
        json.dumps(
            [
                [e.after, e.before, e.reason, e.target_id, e.error_code]
                for e in AuditEntry.objects.all()
            ],
            default=str,
        ),
        json.dumps(dict(client.session.items()), default=str),
        str(captured),
        caplog.text,
        capsys.readouterr().out,
    ]
    assert AuditEntry.objects.filter(action="shop.create_promo").exists()
    for text in haystacks:
        for code in CODES:
            assert code not in text
    # сообщения (хранилище сообщений кладёт их в куку/сессию) тоже без кодов
    assert all(code not in str(client.cookies) for code in CODES)
    # повторный GET создания кодов не показывает
    assert CODES[0] not in client.get("/shop/promos/new/").content.decode()


def test_promo_item_reward_uses_active_items(client: Any, login: Any, progression: Any) -> None:
    login()
    items_route = _stub_items(progression, _item(active=True))
    route = progression.route("POST", PROMO).respond(
        201, json={"id": BATCH, "label": "лето", "codes": CODES}
    )
    page = client.get("/shop/promos/new/").content.decode()
    assert f'<option value="{ITEM}">' in page
    assert items_route.calls.last.request.url.params["active"] == "true"
    client.post(
        "/shop/promos/new/",
        _promo_form(
            reward_kind="item", item_id=ITEM, verses_amount="", expires_at="2027-01-01T12:00"
        ),
    )
    body = json.loads(route.calls.last.request.content)
    assert body["reward"] == {"kind": "item", "item_id": ITEM}
    assert body["expires_at"].startswith("2027-01-01T12:00")


def test_promo_validation_before_service(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_items(progression)
    route = progression.route("POST", PROMO).respond(
        201, json={"id": BATCH, "label": "лето", "codes": CODES}
    )
    for patch in (
        {"reason": ""},
        {"count": "1001"},
        {"verses_amount": ""},
        {"reward_kind": "item", "item_id": ""},
    ):
        assert client.post("/shop/promos/new/", _promo_form(**patch)).status_code == 400
    assert route.call_count == 0
    assert not AuditEntry.objects.exists()


def test_promo_repeat_post_shows_codes_already_shown_message(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_items(progression)
    progression.route("POST", PROMO).respond(
        409, json=problem(409, "progression.codes_already_shown")
    )
    response = client.post("/shop/promos/new/", _promo_form())
    page = response.content.decode()
    assert response.status_code == 400
    assert "отзовите партию и создайте новую" in page
    assert "no-store" in response["Cache-Control"]
    entry = AuditEntry.objects.get(action="shop.create_promo")
    assert entry.error_code == "progression.codes_already_shown"


@pytest.mark.parametrize(
    "code",
    [
        "progression.promo_batch_too_large",
        "progression.promo_actor_daily_cap_exceeded",
        "progression.promo_total_daily_cap_exceeded",
    ],
)
def test_promo_caps_show_limit_used_requested(
    client: Any, login: Any, progression: Any, problem: Any, code: str
) -> None:
    login()
    _stub_items(progression)
    progression.route("POST", PROMO).respond(
        422, json=problem(422, code) | {"details": {"limit": 1000, "used": 800, "requested": 500}}
    )
    page = client.post("/shop/promos/new/", _promo_form()).content.decode()
    assert "лимит 1000" in page
    assert "использовано 800" in page
    assert "запрошено 500" in page


def test_promo_revoke_requires_reason_and_audits(client: Any, login: Any, progression: Any) -> None:
    login()
    route = progression.route("POST", f"{PROMO}/{BATCH}/revoke").respond(204)
    assert client.get(f"/shop/promos/{BATCH}/revoke/").status_code == 200
    empty = client.post(f"/shop/promos/{BATCH}/revoke/", {"reason": "", "idempotency_key": _key()})
    assert empty.status_code == 400
    assert route.call_count == 0
    key = _key()
    response = client.post(
        f"/shop/promos/{BATCH}/revoke/", {"reason": "утечка кодов", "idempotency_key": key}
    )
    assert response["Location"] == "/shop/promos/"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"reason": "утечка кодов"}
    entry = AuditEntry.objects.get(action="shop.revoke_promo")
    assert (entry.target_id, entry.reason) == (BATCH, "утечка кодов")


def test_promo_revoke_409_message(client: Any, login: Any, progression: Any, problem: Any) -> None:
    login()
    progression.route("POST", f"{PROMO}/{BATCH}/revoke").respond(
        409, json=problem(409, "resource.conflict")
    )
    response = client.post(
        f"/shop/promos/{BATCH}/revoke/", {"reason": "утечка кодов", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert AuditEntry.objects.get(action="shop.revoke_promo").error_code == "resource.conflict"


# ------------------------------------------------------------------------------ ошибки сервиса


def test_item_card_distinguishes_service_errors_from_not_found(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_config(progression)
    route = progression.route("GET", ITEMS).respond(403, json=problem(403, "auth.forbidden"))
    forbidden = client.get(f"/shop/items/{ITEM}/")
    assert forbidden.status_code == 403
    assert "Недостаточно прав" in forbidden.content.decode()
    route.respond_off_contract(503, json=problem(503, "dependency.unavailable"))
    down = client.get(f"/shop/items/{ITEM}/")
    assert down.status_code == 503
    assert "временно недоступен" in down.content.decode()


def test_item_search_stops_at_page_with_item(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    route = progression.route("GET", ITEMS).respond(
        200, json={"items": [_item()], "next_cursor": "more"}
    )
    assert client.get(f"/shop/items/{ITEM}/").status_code == 200
    assert route.call_count == 1


def test_item_search_walks_pages_until_found(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    route = progression.route("GET", ITEMS)
    pages = iter(
        [
            {"items": [_item(id=str(uuid.uuid4()))], "next_cursor": "p2"},
            {"items": [_item()], "next_cursor": "p3"},
            {"items": [], "next_cursor": None},
        ]
    )
    route.mock(side_effect=lambda _: httpx.Response(200, json=next(pages)))
    assert client.get(f"/shop/items/{ITEM}/").status_code == 200
    assert route.call_count == 2


def test_pack_form_reports_items_load_failure(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("GET", ITEMS).respond_off_contract(
        503, json=problem(503, "dependency.unavailable")
    )
    page = client.get("/shop/packs/new/").content.decode()
    assert "Не удалось загрузить список предметов" in page
    route = progression.route("POST", "/admin/v1/packs").respond(201, json=_pack())
    response = client.post(
        "/shop/packs/new/",
        {
            "key": "starter",
            "title": "Стартовый",
            "style": "bright",
            "item_ids": [ITEM],
            "verses_amount": "10",
            "price_kopecks": "9900",
            "reason": "новый пак",
            "idempotency_key": _key(),
        },
    )
    text = response.content.decode()
    assert response.status_code == 503
    assert "Не удалось загрузить список предметов" in text
    assert "Выберите корректный вариант" not in text
    assert route.call_count == 0


def test_promo_form_reports_items_load_failure(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("GET", ITEMS).respond(403, json=problem(403, "auth.forbidden"))
    page = client.get("/shop/promos/new/").content.decode()
    assert "Не удалось загрузить список предметов" in page
    route = progression.route("POST", PROMO).respond(
        201, json={"id": BATCH, "label": "лето", "codes": CODES}
    )
    response = client.post(
        "/shop/promos/new/", _promo_form(reward_kind="item", item_id=ITEM, verses_amount="")
    )
    text = response.content.decode()
    assert response.status_code == 403
    assert "Не удалось загрузить список предметов" in text
    assert "Выберите корректный вариант" not in text
    assert route.call_count == 0
    # награда Версами предметов не требует
    assert client.post("/shop/promos/new/", _promo_form()).status_code == 200


def test_pack_edit_distinguishes_service_error_from_not_found(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    _stub_items(progression)
    route = progression.route("GET", "/admin/v1/packs").respond(
        403, json=problem(403, "auth.forbidden")
    )
    assert client.get(f"/shop/packs/{PACK}/").status_code == 403
    route.respond(200, json={"items": []})
    assert client.get(f"/shop/packs/{PACK}/").status_code == 404


def test_media_form_ids_do_not_clash(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    _stub_items(progression)
    page = client.get(f"/shop/items/{ITEM}/").content.decode()
    assert page.count('id="id_reason"') == 1
    assert page.count('id="id_media_reason"') == 1
    assert page.count('id="id_media_idempotency_key"') == 1


def test_pagination_links_are_url_encoded(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_config(progression)
    progression.route("GET", ITEMS).respond_off_contract(
        200, json={"items": [_item()], "next_cursor": "c&x=1"}
    )
    page = client.get("/shop/?slot=a%26b").content.decode()
    assert "cursor=c%26x%3D1" in page
    assert "slot=a%26b" in page
    progression.route("GET", PROMO).respond_off_contract(
        200, json={"items": [_batch()], "next_cursor": "c&x=1"}
    )
    assert "cursor=c%26x%3D1" in client.get("/shop/promos/").content.decode()
