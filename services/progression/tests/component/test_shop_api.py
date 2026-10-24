"""Публичный API 5b: магазин, инвентарь, оформление, паки, колесо (ADR 0045)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.application.shop import ShopService

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
ITEM = "0192a3b4-c5d6-7e8f-9a0b-0000000c0001"
UID = "0192a3b4-c5d6-7e8f-9a0b-0000000b0001"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PRIVATE = "private, no-store"

PUBLIC: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/shop/items", {}),
    ("POST", f"/v1/shop/items/{ITEM}/purchase", {}),
    ("GET", "/v1/shop/packs", {}),
    ("GET", "/v1/me/inventory", {}),
    ("GET", f"/v1/appearance?user_ids={UID}", {}),
    ("GET", "/v1/me/appearance", {}),
    ("PUT", "/v1/me/appearance/frame", {"json": {"item_id": ITEM}}),
    ("PUT", "/v1/me/appearance/frame", {"json": {"item_id": None}}),
    ("POST", "/v1/promo/redeem", {"json": {"code": "ABCD-EFGH-JKLM"}}),
    ("GET", "/v1/me/avatar", {}),
    ("POST", "/v1/me/avatar", {"files": {"file": ("a.png", PNG, "image/png")}}),
]


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


# ------------------------------------------------------------------ доступ и формат запроса
@pytest.mark.parametrize(("method", "path", "kwargs"), PUBLIC)
async def test_public_routes_need_an_account(
    progression: Service, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    await progression.publish_config()

    async def call(token: str | None) -> httpx.Response:
        headers = dict(KEY)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await progression.client.request(method, path, headers=headers, **kwargs)

    assert_problem(await call(None), 401, "auth.unauthenticated")
    assert_problem(await call(progression.issuer.issue_guest()), 403, "auth.guest_forbidden")
    allowed = await call(progression.issuer.issue())
    assert allowed.status_code != 501, allowed.text


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("POST", f"/v1/shop/items/{ITEM}/purchase", {}),
        ("POST", "/v1/promo/redeem", {"json": {"code": "ABCD-EFGH-JKLM"}}),
    ],
)
async def test_public_mutations_require_idempotency_key(
    progression: Service, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    response = await progression.client.request(method, path, headers=progression.user(), **kwargs)
    assert_problem(response, 422, "idempotency.key_required")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/v1/me/appearance/frame", {"item_id": ITEM, "price": 1}),
        ("POST", "/v1/promo/redeem", {"code": "ABCD-EFGH-JKLM", "reward": {"verses": 999}}),
    ],
)
async def test_public_bodies_reject_extra_fields_such_as_price_or_reward(
    progression: Service, method: str, path: str, body: dict[str, Any]
) -> None:
    """Цену и награду определяет сервер: лишнее поле клиента - 422, а не молчаливый пропуск."""
    headers = {**progression.user(), **KEY}
    response = await progression.client.request(method, path, headers=headers, json=body)
    assert response.status_code == 422


async def test_appearance_batch_is_limited_to_twelve_ids(progression: Service) -> None:
    ids = "&".join(f"user_ids={uuid7()}" for _ in range(13))
    response = await progression.client.get(f"/v1/appearance?{ids}", headers=progression.user())
    assert response.status_code == 422


# ------------------------------------------------------------------ витрина
async def test_catalog_shows_states_and_balance_with_private_cache_header(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    cheap = await progression.add_item("cheap", price=10)
    locked = await progression.add_item("locked", price=None, level_required=5)
    await progression.add_item("hidden", price=10, active=False)
    await progression.grant_verses(user, 10)
    assert_ok = await progression.purchase(user, cheap)
    assert assert_ok.status_code == 200, assert_ok.text

    response = await progression.get("/v1/shop/items", user)

    assert response.status_code == 200
    assert response.headers["cache-control"] == PRIVATE
    body = response.json()
    states = {i["item"]["key"]: i["state"] for i in body["items"]}
    assert states == {"cheap": "owned", "locked": "locked"}
    assert body["balance"] == 0
    assert {i["item"]["id"] for i in body["items"]} == {str(cheap), str(locked)}
    assert all(
        i["item"]["asset_url"].startswith("/cosmetics/c/")
        for i in body["items"]
        if i["item"]["key"] == "cheap"
    )


async def test_catalog_can_be_filtered_by_slot(progression: Service) -> None:
    await progression.publish_config()
    await progression.add_item("f1", slot="frame")
    await progression.add_item("t1", slot="title")

    body = (await progression.get("/v1/shop/items?slot=title", uuid7())).json()

    assert [i["item"]["key"] for i in body["items"]] == ["t1"]


async def test_without_shop_section_the_shop_is_empty(progression: Service) -> None:
    await progression.publish_config(shop=...)
    await progression.add_item("f1")

    body = (await progression.get("/v1/shop/items", uuid7())).json()

    assert body["items"] == []


# ------------------------------------------------------------------ покупка
async def test_purchase_debits_grants_and_publishes_in_one_go(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item("frame_gold", price=30)
    await progression.grant_verses(user, 100)

    response = await progression.purchase(user, item)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    body = response.json()
    assert body["repeated"] is False
    assert body["balance_after"] == 70
    assert body["item"]["key"] == "frame_gold"
    assert await progression.balance(user) == 70
    (entry,) = await progression.rows("currency_ledger", user, op_id=f"shop:{user}:{item}")
    assert (entry.direction, entry.amount, entry.bucket) == ("debit", 30, "earned")
    (owned,) = await progression.rows("inventory", user)
    assert (owned.item_id, owned.source) == (item, "purchase")
    (event,) = await progression.outbox("progression.ItemPurchased")
    assert event.payload["price"] == 30 and event.payload["balance_after"] == 70
    assert event.payload["op_id"] == f"shop:{user}:{item}"
    assert_event_matches_schema(event)
    debits = await progression.outbox("progression.CurrencyDebited")
    assert [e.payload["op_id"] for e in debits] == [f"shop:{user}:{item}"]


async def test_repeated_purchase_returns_the_same_grant_without_new_posting(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item(price=30)
    await progression.grant_verses(user, 100)

    first = await progression.purchase(user, item)
    again = await progression.purchase(user, item, key="idem-another-key-1")

    assert again.status_code == 200
    assert again.json()["repeated"] is True
    assert again.json()["balance_after"] == first.json()["balance_after"] == 70
    assert len(await progression.rows("currency_ledger", user, direction="debit")) == 1
    assert len(await progression.outbox("progression.ItemPurchased")) == 1


async def test_insufficient_funds_is_409_and_changes_nothing(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item(price=30)
    await progression.grant_verses(user, 29)

    response = await progression.purchase(user, item)

    assert_problem(response, 409, "progression.insufficient_funds")
    assert await progression.balance(user) == 29
    assert await progression.rows("inventory", user) == []
    assert await progression.outbox("progression.ItemPurchased") == []


async def test_already_owned_by_level_is_409(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item(price=10, level_required=2)
    await progression.grant_verses(user, 50)
    await progression.set_level(user, 2)

    response = await progression.purchase(user, item)

    assert_problem(response, 409, "progression.already_owned")
    assert await progression.balance(user) == 50


async def test_item_without_price_is_not_available_for_purchase(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item(price=None, level_required=9)

    assert_problem(await progression.purchase(user, item), 403, "progression.item_not_available")


@pytest.mark.parametrize("kind", ["missing", "inactive"])
async def test_unknown_or_inactive_item_is_404(progression: Service, kind: str) -> None:
    await progression.publish_config()
    item = uuid7() if kind == "missing" else await progression.add_item(active=False)

    assert_problem(await progression.purchase(uuid7(), item), 404, "progression.item_not_found")


async def test_price_is_taken_from_the_server_only(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item(price=30)
    await progression.grant_verses(user, 100)
    headers = {**progression.user(user), **idem()}

    response = await progression.client.post(
        f"/v1/shop/items/{item}/purchase?price=1", headers=headers
    )

    assert response.status_code == 200
    assert await progression.balance(user) == 70


# ------------------------------------------------------------------ уровень
async def test_level_items_are_granted_lazily_without_posting(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item("by_level", price=None, level_required=2)
    await progression.add_item("later", price=None, level_required=9)
    await progression.set_level(user, 2)
    ledger_before = await progression.verse_rows(user)

    inventory = (await progression.get("/v1/me/inventory", user)).json()

    assert [(i["item"]["key"], i["source"]) for i in inventory["items"]] == [("by_level", "level")]
    assert await progression.verse_rows(user) == ledger_before
    (row,) = await progression.rows("inventory", user)
    assert row.item_id == item
    # повторное чтение ничего не меняет
    again = (await progression.get("/v1/me/inventory", user)).json()
    assert len(again["items"]) == 1


async def test_inventory_hides_expired_items(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item(price=10)
    await progression.grant_verses(user, 10)
    await progression.purchase(user, item)
    assert len((await progression.get("/v1/me/inventory", user)).json()["items"]) == 1
    assert progression.infra.sessions is not None
    from sqlalchemy import text

    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            text("UPDATE inventory SET expires_at = :t WHERE user_id = :u"),
            {"t": progression.clock.now() - timedelta(seconds=1), "u": user},
        )

    assert (await progression.get("/v1/me/inventory", user)).json()["items"] == []


# ------------------------------------------------------------------ оформление
async def owned_item(
    progression: Service, user: UUID, key: str = "f1", slot: str = "frame"
) -> UUID:
    item = await progression.add_item(key, slot=slot, price=10)
    await progression.grant_verses(user, 10)
    assert (await progression.purchase(user, item)).status_code == 200
    return item


async def equip(progression: Service, user: UUID, slot: str, item: UUID | None) -> httpx.Response:
    return await progression.client.put(
        f"/v1/me/appearance/{slot}",
        json={"item_id": None if item is None else str(item)},
        headers=progression.user(user),
    )


async def test_equip_and_unequip_publish_one_event_per_change(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await owned_item(progression, user)

    first = await equip(progression, user, "frame", item)
    repeat = await equip(progression, user, "frame", item)

    assert first.status_code == repeat.status_code == 200
    assert first.headers["cache-control"] == PRIVATE
    slots = {s["slot"]: s["item"] for s in first.json()["slots"]}
    assert slots["frame"]["id"] == str(item) and slots["avatar"] is None
    assert len(await progression.outbox("progression.ItemEquipped")) == 1

    cleared = await equip(progression, user, "frame", None)
    assert {s["slot"]: s["item"] for s in cleared.json()["slots"]}["frame"] is None
    await equip(progression, user, "frame", None)  # снять пустой слот - без события
    events = await progression.outbox("progression.ItemEquipped")
    assert [e.payload["item_id"] for e in events] == [str(item), None]
    for event in events:
        assert_event_matches_schema(event)


async def test_equip_errors(progression: Service) -> None:
    await progression.publish_config()
    user, other = uuid7(), uuid7()
    mine = await owned_item(progression, user)
    theirs = await owned_item(progression, other, key="f2")

    assert_problem(await equip(progression, user, "wings", mine), 404, "progression.slot_not_found")
    assert_problem(
        await equip(progression, user, "frame", theirs), 404, "progression.item_not_owned"
    )
    assert_problem(
        await equip(progression, user, "frame", uuid7()), 404, "progression.item_not_owned"
    )
    assert_problem(
        await equip(progression, user, "avatar", mine), 422, "progression.item_slot_mismatch"
    )
    assert await progression.outbox("progression.ItemEquipped") == []


async def test_my_appearance_lists_slots_in_config_order(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await owned_item(progression, user)
    await equip(progression, user, "frame", item)

    response = await progression.get("/v1/me/appearance", user)

    assert response.headers["cache-control"] == PRIVATE
    assert [s["slot"] for s in response.json()["slots"]] == [
        "avatar",
        "frame",
        "title",
        "background",
    ]


async def test_public_appearance_batch_dedupes_and_skips_unknown(progression: Service) -> None:
    await progression.publish_config()
    a, b, ghost = uuid7(), uuid7(), uuid7()
    item = await owned_item(progression, a)
    await equip(progression, a, "frame", item)
    await progression.set_level(b, 1)

    response = await progression.client.get(
        f"/v1/appearance?user_ids={a}&user_ids={b}&user_ids={a}&user_ids={ghost}",
        headers=progression.user(),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == PRIVATE
    players = response.json()["players"]
    assert [p["user_id"] for p in players] == [str(a), str(b)]
    frame = {s["slot"]: s["item"] for s in players[0]["appearance"]["slots"]}["frame"]
    assert frame["key"] == "f1"
    assert all(s["item"] is None for s in players[1]["appearance"]["slots"])


# ------------------------------------------------------------------ паки
async def test_packs_are_hidden_while_billing_is_disabled(progression: Service) -> None:
    await progression.publish_config()

    response = await progression.get("/v1/shop/packs", uuid7())

    assert_problem(response, 404, "progression.billing_disabled")


async def test_packs_are_listed_when_billing_is_enabled(progression: Service) -> None:
    await progression.publish_config()
    item = await progression.add_item(price=10)
    from progression_service.domain.packs import Pack

    now = progression.clock.now()
    async with progression.container.uow_factory()() as uow:
        await uow.packs.add(
            Pack(
                uuid7(),
                "winter",
                "Зима",
                "winter",
                (item,),
                0,
                19900,
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        await uow.commit()
    service = ShopService(
        progression.container.uow_factory(), progression.clock, billing_enabled=True
    )

    ((pack, items),) = await service.packs()

    assert pack.key == "winter" and [i.id for i in items] == [item]


# ------------------------------------------------------------------ колесо
async def test_wheel_item_goes_to_inventory_and_duplicate_pays_verses_by_rarity(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item("wheel_cosmetic", price=None, level_required=50)
    progression.rig_wheel("item_cosmetic")

    first = await progression.spin(user)
    assert first.status_code == 200, first.text
    (row,) = await progression.rows("inventory", user)
    assert (row.item_id, row.source) == (item, "wheel")
    assert await progression.rows("grants_log", user) == []
    assert await progression.verse_rows(user) == 0

    progression.clock.advance(days=1)
    second = await progression.spin(user)
    assert second.status_code == 200
    day = second.json()["day"]
    assert len(await progression.rows("inventory", user)) == 1
    (dup,) = await progression.rows("currency_ledger", user)
    assert dup.op_id == f"wheel:{user}:{day}:dup"
    assert (dup.amount, dup.bucket, dup.direction) == (15, "earned", "credit")
    assert await progression.balance(user) == 15
    # повтор спина того же дня не двоит компенсацию
    again = await progression.spin(user)
    assert again.json()["repeated"] is True
    assert await progression.verse_rows(user) == 1
    (spun, _) = await progression.outbox("progression.WheelSpun")
    assert_event_matches_schema(spun)


async def test_wheel_item_missing_in_table_does_not_break_the_spin(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("item_cosmetic")

    response = await progression.spin(user)

    assert response.status_code == 200
    (grant,) = await progression.rows("grants_log", user)
    assert grant.item_key == "wheel_cosmetic"
