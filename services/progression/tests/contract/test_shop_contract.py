"""Контракты 5b: ответы реализации против снимка OpenAPI, события S1 против схем, заголовки."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.contract

Service = Any  # фикстура `progression` описана в conftest.py
SNAPSHOT = json.loads(
    (Path(__file__).resolve().parents[4] / "contracts" / "openapi" / "progression.json").read_text(
        encoding="utf-8"
    )
)
ADMIN_ID = UUID(int=80)
REASON = {"reason": "проверка контракта"}
PRIVATE = "private, no-store"


def conforms(template: str, method: str, response: httpx.Response) -> None:
    """Тело ответа соответствует схеме снимка OpenAPI для его статуса (или схеме Problem)."""
    operation = SNAPSHOT["paths"][template][method.lower()]
    declared = operation["responses"]
    spec = declared.get(str(response.status_code))
    assert spec is not None, f"{method} {template}: статус {response.status_code} не описан"
    if response.status_code == 204:
        return
    media = "application/problem+json" if response.status_code >= 400 else "application/json"
    schema = spec["content"][media]["schema"]
    validator = Draft202012Validator({**schema, "components": SNAPSHOT["components"]})
    errors = [e.message for e in validator.iter_errors(response.json())]
    assert not errors, f"{method} {template} {response.status_code}: {errors}"


def admin_headers(svc: Service, perm: str) -> dict[str, str]:
    token = svc.issuer.issue(user_id=ADMIN_ID, perms=("admin.access", perm))
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"}


def webp(frames: int = 1) -> bytes:
    images = [Image.new("RGBA", (16, 16), (i * 20 % 256, 1, 2, 255)) for i in range(frames)]
    out = io.BytesIO()
    images[0].save(out, format="WEBP", save_all=True, append_images=images[1:], duration=50)
    return out.getvalue()


async def test_player_responses_match_the_openapi_snapshot(progression: Service) -> None:
    svc = progression
    await svc.publish_config()
    user = uuid7()
    item = await svc.add_item("frame_gold", price=30)
    await svc.grant_verses(user, 100)

    purchase = await svc.purchase(user, item)
    conforms("/v1/shop/items/{item_id}/purchase", "POST", purchase)
    conforms("/v1/shop/items/{item_id}/purchase", "POST", await svc.purchase(user, item))
    conforms("/v1/shop/items/{item_id}/purchase", "POST", await svc.purchase(user, uuid7()))
    conforms("/v1/shop/items", "GET", await svc.get("/v1/shop/items", user))
    conforms("/v1/me/inventory", "GET", await svc.get("/v1/me/inventory", user))
    conforms("/v1/me/appearance", "GET", await svc.get("/v1/me/appearance", user))
    equip = await svc.client.put(
        "/v1/me/appearance/frame", json={"item_id": str(item)}, headers=svc.user(user)
    )
    conforms("/v1/me/appearance/{slot}", "PUT", equip)
    conforms(
        "/v1/me/appearance/{slot}",
        "PUT",
        await svc.client.put(
            "/v1/me/appearance/wings", json={"item_id": None}, headers=svc.user(user)
        ),
    )
    batch = await svc.client.get(f"/v1/appearance?user_ids={user}", headers=svc.user())
    conforms("/v1/appearance", "GET", batch)
    conforms("/v1/shop/packs", "GET", await svc.get("/v1/shop/packs", user))
    wrong = await svc.client.post(
        "/v1/promo/redeem",
        json={"code": "AAAA-BBBB-CCCC"},
        headers={**svc.user(user), "Idempotency-Key": "idem-0123456789"},
    )
    conforms("/v1/promo/redeem", "POST", wrong)


async def test_admin_responses_match_the_openapi_snapshot(progression: Service) -> None:
    svc = progression
    await svc.publish_config()
    items = lambda: admin_headers(svc, "progression.items.manage")  # noqa: E731
    promo = lambda: admin_headers(svc, "progression.promo.manage")  # noqa: E731
    body = {
        "key": "frame_gold",
        "slot": "frame",
        "rarity": "rare",
        "title": "Рамка",
        "media_kind": "animated",
        "price": 30,
        **REASON,
    }
    created = await svc.client.post("/admin/v1/items", json=body, headers=items())
    assert created.status_code == 201
    conforms("/admin/v1/items", "POST", created)
    item_id = created.json()["id"]
    uploaded = await svc.client.put(
        f"/admin/v1/items/{item_id}/media",
        data=REASON,
        files={"file": ("a.webp", webp(frames=3), "image/webp")},
        headers=items(),
    )
    conforms("/admin/v1/items/{item_id}/media", "PUT", uploaded)
    conforms(
        "/admin/v1/items/{item_id}",
        "PUT",
        await svc.client.put(
            f"/admin/v1/items/{item_id}", json={**body, "active": True}, headers=items()
        ),
    )
    conforms("/admin/v1/items", "GET", await svc.client.get("/admin/v1/items", headers=items()))
    conforms(
        "/admin/v1/items",
        "POST",
        await svc.client.post("/admin/v1/items", json=body, headers=items()),
    )
    pack = await svc.client.post(
        "/admin/v1/packs",
        json={
            "key": "winter",
            "title": "Зима",
            "style": "winter",
            "item_ids": [item_id],
            "price_kopecks": 19900,
            **REASON,
        },
        headers=items(),
    )
    conforms("/admin/v1/packs", "POST", pack)
    conforms("/admin/v1/packs", "GET", await svc.client.get("/admin/v1/packs", headers=items()))
    batch = await svc.client.post(
        "/admin/v1/promo-codes",
        json={
            "label": "Весна",
            "reward": {"kind": "item", "item_id": item_id},
            "count": 2,
            "max_uses": 1,
            **REASON,
        },
        headers=promo(),
    )
    conforms("/admin/v1/promo-codes", "POST", batch)
    conforms(
        "/admin/v1/promo-codes",
        "GET",
        await svc.client.get("/admin/v1/promo-codes", headers=promo()),
    )
    revoke = await svc.client.post(
        f"/admin/v1/promo-codes/{batch.json()['id']}/revoke", json=REASON, headers=promo()
    )
    conforms("/admin/v1/promo-codes/{promo_id}/revoke", "POST", revoke)


async def test_player_and_admin_successes_are_never_cached(progression: Service) -> None:
    svc = progression
    await svc.publish_config()
    user = uuid7()
    item = await svc.add_item(price=10)
    await svc.grant_verses(user, 10)
    admin = admin_headers(svc, "progression.items.manage")
    responses = [
        await svc.purchase(user, item),
        await svc.get("/v1/shop/items", user),
        await svc.get("/v1/me/inventory", user),
        await svc.get("/v1/me/appearance", user),
        await svc.client.put(
            "/v1/me/appearance/frame", json={"item_id": None}, headers=svc.user(user)
        ),
        await svc.client.get(f"/v1/appearance?user_ids={user}", headers=svc.user(user)),
        await svc.client.get("/admin/v1/items", headers=admin),
        await svc.client.get("/admin/v1/packs", headers=admin),
        await svc.client.get(
            "/admin/v1/promo-codes", headers=admin_headers(svc, "progression.promo.manage")
        ),
    ]
    for response in responses:
        assert response.status_code in (200, 201), response.text
        expected = "no-store" if response.request.url.path.startswith("/admin") else PRIVATE
        assert response.headers["cache-control"] == expected, response.request.url


async def test_error_responses_carry_no_cache_header_of_success(progression: Service) -> None:
    await progression.publish_config()
    response = await progression.purchase(uuid7(), uuid7())
    assert_problem(response, 404, "progression.item_not_found")
    assert response.headers.get("cache-control") != PRIVATE


async def test_runtime_schemas_equal_the_snapshot_for_shop_models(progression: Service) -> None:
    runtime = (await progression.client.get("/openapi.json")).json()["components"]["schemas"]
    names = (
        "ShopCatalog",
        "PurchaseResult",
        "MyInventory",
        "Appearance",
        "EquipRequest",
        "PlayersAppearance",
        "PromoRedeemRequest",
        "PromoRedeemResult",
        "PackCatalog",
        "AdminItem",
        "AdminItemPage",
        "AdminPack",
        "CreatedPromo",
        "PromoBatchPage",
        "CreatePromoRequest",
        "SavePackRequest",
    )
    for name in names:
        assert runtime[name] == SNAPSHOT["components"]["schemas"][name], name


async def test_events_of_stream_s1_match_their_schemas(progression: Service) -> None:
    svc = progression
    await svc.publish_config()
    user = uuid7()
    item = await svc.add_item("frame_gold", price=10)
    await svc.grant_verses(user, 10)
    await svc.purchase(user, item)
    await svc.client.put(
        "/v1/me/appearance/frame", json={"item_id": str(item)}, headers=svc.user(user)
    )
    batch = await svc.client.post(
        "/admin/v1/promo-codes",
        json={
            "label": "Весна",
            "reward": {"kind": "verses", "verses_amount": 5},
            "count": 1,
            "max_uses": 1,
            **REASON,
        },
        headers=admin_headers(svc, "progression.promo.manage"),
    )
    await svc.client.post(
        "/v1/promo/redeem",
        json={"code": batch.json()["codes"][0]},
        headers={**svc.user(user), "Idempotency-Key": "idem-0123456789"},
    )

    for event_type in (
        "progression.ItemPurchased",
        "progression.ItemEquipped",
        "progression.PromoRedeemed",
        "progression.CurrencyDebited",
        "progression.CurrencyCredited",
    ):
        events = await svc.outbox(event_type)
        assert events, event_type
        for event in events:
            assert_event_matches_schema(event)
            assert event.aggregate_id == str(user)
