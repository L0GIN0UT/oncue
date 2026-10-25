"""Admin API 5b: предметы, картинки, паки (ADR 0045, 0046)."""

from __future__ import annotations

import io
import os
from typing import Any
from uuid import UUID

import aioboto3
import httpx
import pytest
from botocore.exceptions import ClientError
from PIL import Image
from progression_service.infrastructure.db.tables_admin import admin_actions
from sqlalchemy import select

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
ADMIN_ID = UUID(int=77)
ITEMS = "progression.items.manage"
PROMO = "progression.promo.manage"
REASON = {"reason": "проверка контракта"}
ITEM_BODY = {
    "key": "frame_gold",
    "slot": "frame",
    "rarity": "rare",
    "title": "Золотая рамка",
    "media_kind": "static",
    "price": 30,
    **REASON,
}
ID = "0192a3b4-c5d6-7e8f-9a0b-0000000c0001"
PACK_BODY = {
    "key": "winter",
    "title": "Зимний набор",
    "style": "winter",
    "item_ids": [ID],
    "price_kopecks": 19900,
    **REASON,
}
PROMO_BODY = {
    "label": "Весна",
    "reward": {"kind": "verses", "verses_amount": 50},
    "count": 3,
    "max_uses": 1,
    **REASON,
}
ADMIN_CASES: list[tuple[str, str, dict[str, Any], str]] = [
    ("GET", "/admin/v1/items", {}, ITEMS),
    ("GET", f"/admin/v1/items/{ID}", {}, ITEMS),
    ("POST", "/admin/v1/items", {"json": ITEM_BODY}, ITEMS),
    ("PUT", f"/admin/v1/items/{ID}", {"json": ITEM_BODY}, ITEMS),
    (
        "PUT",
        f"/admin/v1/items/{ID}/media",
        {"data": REASON, "files": {"file": ("a.webp", b"RIFF", "image/webp")}},
        ITEMS,
    ),
    ("GET", "/admin/v1/packs", {}, ITEMS),
    ("POST", "/admin/v1/packs", {"json": PACK_BODY}, ITEMS),
    ("PUT", f"/admin/v1/packs/{ID}", {"json": PACK_BODY}, ITEMS),
    ("GET", "/admin/v1/promo-codes", {}, PROMO),
    ("POST", "/admin/v1/promo-codes", {"json": PROMO_BODY}, PROMO),
    ("POST", f"/admin/v1/promo-codes/{ID}/revoke", {"json": REASON}, PROMO),
]


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


async def call(
    svc: Service,
    method: str,
    path: str,
    *,
    perm: str = ITEMS,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    token = svc.issuer.issue(user_id=ADMIN_ID, perms=("admin.access", perm))
    sent = {"Authorization": f"Bearer {token}", **(headers if headers is not None else idem())}
    return await svc.client.request(method, path, headers=sent, **kwargs)


def webp(frames: int = 1, size: tuple[int, int] = (32, 32)) -> bytes:
    images = [Image.new("RGBA", size, (i * 7 % 256, 40, 90, 255)) for i in range(frames)]
    out = io.BytesIO()
    images[0].save(
        out, format="WEBP", save_all=True, append_images=images[1:], duration=50, lossless=True
    )
    return out.getvalue()


def png(size: tuple[int, int], *, noise: bool = False) -> bytes:
    image = (
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
        if noise
        else Image.new("RGB", size, (1, 2, 3))
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def create_item(svc: Service, **patch: Any) -> dict[str, Any]:
    response = await call(svc, "POST", "/admin/v1/items", json={**ITEM_BODY, **patch})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def upload(
    svc: Service, item_id: str, data: bytes, name: str = "a.webp", mime: str = "image/webp"
) -> httpx.Response:
    return await call(
        svc,
        "PUT",
        f"/admin/v1/items/{item_id}/media",
        data=REASON,
        files={"file": (name, data, mime)},
    )


async def exists(svc: Service, url: str) -> bool:
    settings = svc.infra.settings
    s3 = settings.s3
    key = url.removeprefix("/cosmetics/")
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=s3.endpoint,
        aws_access_key_id=s3.access_key.get_secret_value(),
        aws_secret_access_key=s3.secret_key.get_secret_value(),
        region_name=s3.region,
    ) as client:
        try:
            await client.head_object(Bucket=settings.progression.cosmetics_bucket, Key=key)
        except ClientError:
            return False
    return True


# ------------------------------------------------------------------ права
@pytest.mark.parametrize("case", ADMIN_CASES, ids=[f"{m} {p}" for m, p, _, _ in ADMIN_CASES])
async def test_admin_routes_need_their_own_permission(
    progression: Service, case: tuple[str, str, dict[str, Any], str]
) -> None:
    method, path, kwargs, perm = case
    await progression.publish_config()
    other = PROMO if perm == ITEMS else ITEMS
    anonymous = await progression.client.request(method, path, headers=idem(), **kwargs)
    assert_problem(anonymous, 401, "auth.unauthenticated")
    for token in (
        progression.issuer.issue(),
        progression.issuer.issue(perms=["admin.access"]),
        progression.issuer.issue(perms=[perm]),
        progression.issuer.issue(perms=["admin.access", other]),
    ):
        response = await progression.client.request(
            method, path, headers={**idem(), "Authorization": f"Bearer {token}"}, **kwargs
        )
        assert_problem(response, 403, "auth.forbidden")
    allowed = await call(progression, method, path, perm=perm, **kwargs)
    assert allowed.status_code not in (401, 403, 501), allowed.text


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("POST", "/admin/v1/items", {"json": ITEM_BODY}),
        ("POST", "/admin/v1/packs", {"json": PACK_BODY}),
        (
            "PUT",
            f"/admin/v1/items/{ID}/media",
            {"data": REASON, "files": {"file": ("a", b"x", "image/webp")}},
        ),
    ],
)
async def test_admin_mutations_require_idempotency_key(
    progression: Service, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    response = await call(progression, method, path, headers={}, **kwargs)
    assert_problem(response, 422, "idempotency.key_required")


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/admin/v1/items", {k: v for k, v in ITEM_BODY.items() if k != "reason"}),
        ("/admin/v1/packs", {k: v for k, v in PACK_BODY.items() if k != "reason"}),
        ("/admin/v1/items", {**ITEM_BODY, "extra": 1}),
        ("/admin/v1/items", {**ITEM_BODY, "reason": "  "}),
    ],
)
async def test_admin_mutations_require_reason_and_reject_extra_fields(
    progression: Service, path: str, body: dict[str, Any]
) -> None:
    response = await call(progression, "POST", path, json=body)
    assert response.status_code == 422


async def test_media_upload_requires_reason(progression: Service) -> None:
    response = await call(
        progression,
        "PUT",
        f"/admin/v1/items/{ID}/media",
        files={"file": ("a.webp", b"x", "image/webp")},
    )
    assert response.status_code == 422


# ------------------------------------------------------------------ предметы
async def test_create_item_is_inactive_audited_and_cache_free(progression: Service) -> None:
    await progression.publish_config()

    response = await call(progression, "POST", "/admin/v1/items", json=ITEM_BODY)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["key"], body["active"], body["asset_url"], body["owners"]) == (
        "frame_gold",
        False,
        None,
        0,
    )
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session:
        (action,) = (await session.execute(select(admin_actions))).all()
    assert (action.actor_id, action.action, action.reason) == (
        ADMIN_ID,
        "item.create",
        REASON["reason"],
    )


async def test_get_item_by_id_matches_the_created_card_and_is_cache_free(
    progression: Service,
) -> None:
    await progression.publish_config()
    created = await create_item(progression)

    response = await call(progression, "GET", f"/admin/v1/items/{created['id']}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == created


async def test_get_unknown_item_is_404_with_service_code(progression: Service) -> None:
    response = await call(progression, "GET", f"/admin/v1/items/{uuid7()}")

    assert_problem(response, 404, "progression.item_not_found")


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"slot": "wings"}, "progression.item_slot_unknown"),
        ({"rarity": "mythic"}, "progression.item_rarity_unknown"),
        ({"price": None}, "progression.item_unlock_required"),
        ({"active": True}, "progression.item_media_missing"),
    ],
)
async def test_create_item_validates_against_the_current_config(
    progression: Service, patch: dict[str, Any], code: str
) -> None:
    await progression.publish_config()

    response = await call(progression, "POST", "/admin/v1/items", json={**ITEM_BODY, **patch})

    assert_problem(response, 422, code)


async def test_duplicate_key_is_409_and_unconfigured_shop_rejects_slots(
    progression: Service,
) -> None:
    await progression.publish_config()
    await create_item(progression)
    assert_problem(
        await call(progression, "POST", "/admin/v1/items", json=ITEM_BODY),
        409,
        "progression.item_key_taken",
    )
    await progression.publish_config(version=2, shop=...)
    assert_problem(
        await call(progression, "POST", "/admin/v1/items", json={**ITEM_BODY, "key": "other"}),
        422,
        "progression.item_slot_unknown",
    )


async def test_same_idempotency_key_replays_without_second_item(progression: Service) -> None:
    await progression.publish_config()
    headers = idem()

    first = await call(progression, "POST", "/admin/v1/items", json=ITEM_BODY, headers=headers)
    again = await call(progression, "POST", "/admin/v1/items", json=ITEM_BODY, headers=headers)
    other = await call(
        progression, "POST", "/admin/v1/items", json={**ITEM_BODY, "price": 31}, headers=headers
    )

    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert_problem(other, 409, "idempotency.key_reused")
    assert len(await progression.rows("items")) == 1


async def test_update_item_changes_price_but_not_key(progression: Service) -> None:
    await progression.publish_config()
    item = await create_item(progression)
    path = f"/admin/v1/items/{item['id']}"

    changed = await call(progression, "PUT", path, json={**ITEM_BODY, "price": 99})
    renamed = await call(progression, "PUT", path, json={**ITEM_BODY, "key": "renamed"})
    missing = await call(progression, "PUT", f"/admin/v1/items/{uuid7()}", json=ITEM_BODY)

    assert changed.status_code == 200 and changed.json()["price"] == 99
    assert_problem(renamed, 422, "progression.item_key_immutable")
    assert_problem(missing, 404, "progression.item_not_found")


async def test_list_items_filters_and_pages(progression: Service) -> None:
    await progression.publish_config()
    for n in range(3):
        await create_item(progression, key=f"it_{n}")
        progression.clock.advance(seconds=1)

    first = (await call(progression, "GET", "/admin/v1/items?limit=2")).json()
    second = (
        await call(progression, "GET", f"/admin/v1/items?limit=2&cursor={first['next_cursor']}")
    ).json()
    active = (await call(progression, "GET", "/admin/v1/items?active=true")).json()

    assert [i["key"] for i in first["items"]] == ["it_2", "it_1"]
    assert [i["key"] for i in second["items"]] == ["it_0"] and second["next_cursor"] is None
    assert active["items"] == []


# ------------------------------------------------------------------ картинки
async def test_static_upload_activates_and_replacement_changes_key(progression: Service) -> None:
    await progression.publish_config()
    item = await create_item(progression)

    first = await upload(progression, item["id"], webp())
    assert first.status_code == 200, first.text
    url = first.json()["asset_url"]
    assert url.startswith("/cosmetics/c/") and url.endswith(".webp")
    assert first.json()["poster_url"] is None
    assert await exists(progression, url)

    activated = await call(
        progression, "PUT", f"/admin/v1/items/{item['id']}", json={**ITEM_BODY, "active": True}
    )
    assert activated.status_code == 200 and activated.json()["active"] is True

    second = await upload(progression, item["id"], png((16, 16)), "b.png", "image/png")
    new_url = second.json()["asset_url"]
    assert new_url != url and new_url.endswith(".png")
    assert await exists(progression, new_url)
    assert not await exists(progression, url)


async def test_animated_upload_has_poster_and_limits(progression: Service) -> None:
    await progression.publish_config()
    item = await create_item(progression, media_kind="animated")

    ok = await upload(progression, item["id"], webp(frames=4))
    assert ok.status_code == 200, ok.text
    assert ok.json()["poster_url"].endswith(".webp")
    assert await exists(progression, ok.json()["poster_url"])

    static_file = await upload(progression, item["id"], webp(frames=1))
    too_many = await upload(progression, item["id"], webp(frames=121, size=(16, 16)))
    png_file = await upload(progression, item["id"], png((16, 16)), "a.png", "image/png")

    for response in (static_file, too_many, png_file):
        assert_problem(response, 422, "progression.item_media_invalid")


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"not an image at all", "image/webp"),
        (b"RIFF\x10\x00\x00\x00WEBPjunkjunkjunk", "image/webp"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png"),
        (b"GIF89a" + b"\x00" * 40, "image/webp"),  # подмена Content-Type: сигнатура не та
    ],
)
async def test_fake_or_broken_files_are_rejected(
    progression: Service, data: bytes, mime: str
) -> None:
    await progression.publish_config()
    item = await create_item(progression)

    response = await upload(progression, item["id"], data, "a.webp", mime)

    assert_problem(response, 422, "progression.item_media_invalid")
    assert (await progression.rows("items", id=UUID(item["id"])))[0].asset_key is None


async def test_oversized_or_large_side_files_are_rejected(progression: Service) -> None:
    await progression.publish_config()
    item = await create_item(progression)

    noisy = await upload(progression, item["id"], png((512, 512), noise=True), "a.png", "image/png")
    wide = await upload(progression, item["id"], png((600, 20)), "a.png", "image/png")

    assert_problem(noisy, 413, "progression.item_media_too_large")
    assert_problem(wide, 422, "progression.item_media_invalid")


async def test_upload_to_unknown_item_is_404(progression: Service) -> None:
    await progression.publish_config()
    assert_problem(
        await upload(progression, str(uuid7()), webp()), 404, "progression.item_not_found"
    )


# ------------------------------------------------------------------ паки
async def test_pack_crud_works_while_billing_is_disabled(progression: Service) -> None:
    await progression.publish_config()
    item = await create_item(progression)
    body = {**PACK_BODY, "item_ids": [item["id"]]}

    created = await call(progression, "POST", "/admin/v1/packs", json=body)
    assert created.status_code == 201, created.text
    pack_id = created.json()["id"]
    replaced = await call(
        progression, "PUT", f"/admin/v1/packs/{pack_id}", json={**body, "title": "Новый"}
    )
    listed = (await call(progression, "GET", "/admin/v1/packs")).json()

    assert replaced.status_code == 200 and replaced.json()["title"] == "Новый"
    assert [p["title"] for p in listed["items"]] == ["Новый"]
    assert_problem(
        await call(progression, "POST", "/admin/v1/packs", json=body),
        409,
        "progression.pack_key_taken",
    )
    assert_problem(
        await call(progression, "PUT", f"/admin/v1/packs/{uuid7()}", json=body),
        404,
        "progression.pack_not_found",
    )
    # витрина для игроков по-прежнему скрыта
    assert_problem(
        await progression.get("/v1/shop/packs", uuid7()), 404, "progression.billing_disabled"
    )


@pytest.mark.parametrize("kind", ["empty", "dup", "missing"])
async def test_pack_content_is_validated(progression: Service, kind: str) -> None:
    await progression.publish_config()
    item = await create_item(progression)
    item_ids = {"empty": [], "dup": [item["id"], item["id"]], "missing": [str(uuid7())]}[kind]

    response = await call(
        progression, "POST", "/admin/v1/packs", json={**PACK_BODY, "item_ids": item_ids}
    )

    assert response.status_code == 422
