"""Правки после ревью S1: снятые с продажи предметы, промокод без оракулов, ответы 413."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.infrastructure.db.repositories_shop import SqlItems
from sqlalchemy import text
from structlog.testing import capture_logs

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
REASON = {"reason": "проверка контракта"}


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


async def set_active(svc: Service, item: UUID, *, active: bool) -> None:
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session, session.begin():
        await session.execute(
            text("UPDATE items SET active = :a WHERE id = :i"), {"a": active, "i": item}
        )


async def owned_item(svc: Service, user: UUID, key: str = "f1", slot: str = "frame") -> UUID:
    item = await svc.add_item(key, slot=slot, price=10)
    await svc.grant_verses(user, 10)
    assert (await svc.purchase(user, item)).status_code == 200
    return item


async def equip(svc: Service, user: UUID, slot: str, item: UUID | None) -> httpx.Response:
    return await svc.client.put(
        f"/v1/me/appearance/{slot}",
        json={"item_id": None if item is None else str(item)},
        headers=svc.user(user),
    )


async def make_batch(svc: Service, item: UUID | None = None) -> str:
    reward = (
        {"kind": "item", "item_id": str(item)} if item else {"kind": "verses", "verses_amount": 50}
    )
    token = svc.issuer.issue(
        user_id=UUID(int=81), perms=("admin.access", "progression.promo.manage")
    )
    response = await svc.client.post(
        "/admin/v1/promo-codes",
        json={"label": "Т", "reward": reward, "count": 1, "max_uses": 3, **REASON},
        headers={"Authorization": f"Bearer {token}", **idem()},
    )
    assert response.status_code == 201, response.text
    code: str = response.json()["codes"][0]
    return code


async def redeem(svc: Service, user: UUID, code: str) -> httpx.Response:
    return await svc.client.post(
        "/v1/promo/redeem", json={"code": code}, headers={**svc.user(user), **idem()}
    )


def same_problem(left: httpx.Response, right: httpx.Response) -> None:
    """Статус, заголовки и тело совпадают (кроме сквозного ``correlation_id`` запроса)."""

    def body(response: httpx.Response) -> dict[str, Any]:
        data: dict[str, Any] = response.json()
        data.pop("correlation_id", None)
        return data

    assert left.status_code == right.status_code == 404
    assert body(left) == body(right)
    assert left.headers["content-type"] == right.headers["content-type"]


# ------------------------------------------------------------------ снятый с продажи предмет
async def test_deactivated_item_disappears_from_profile_and_public_appearance(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await owned_item(progression, user)
    await equip(progression, user, "frame", item)
    await set_active(progression, item, active=False)

    mine = (await progression.get("/v1/me/appearance", user)).json()
    public = await progression.client.get(
        f"/v1/appearance?user_ids={user}", headers=progression.user()
    )
    after = await equip(progression, user, "title", None)

    assert all(s["item"] is None for s in mine["slots"])
    (player,) = public.json()["players"]
    assert all(s["item"] is None for s in player["appearance"]["slots"])
    assert all(s["item"] is None for s in after.json()["slots"])


async def test_inactive_owned_item_cannot_be_equipped(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await owned_item(progression, user)
    await set_active(progression, item, active=False)

    response = await equip(progression, user, "frame", item)

    assert_problem(response, 404, "progression.item_not_owned")
    assert await progression.outbox("progression.ItemEquipped") == []


async def test_wheel_treats_an_inactive_item_as_missing(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await progression.add_item("wheel_cosmetic", price=None, level_required=50)
    await set_active(progression, item, active=False)
    progression.rig_wheel("item_cosmetic")

    with capture_logs() as logs:
        response = await progression.spin(user)

    assert response.status_code == 200
    assert await progression.rows("inventory", user) == []
    (grant,) = await progression.rows("grants_log", user)
    assert grant.item_key == "wheel_cosmetic"
    assert any(
        e["event"] == "progression.wheel_item_missing" and e["log_level"] == "error" for e in logs
    )


# ------------------------------------------------------------------ промокод - не оракул
async def test_code_for_a_deactivated_item_answers_like_an_invalid_code(
    progression: Service,
) -> None:
    await progression.publish_config()
    item = await progression.add_item("promo_item", price=10)
    code = await make_batch(progression, item)
    await set_active(progression, item, active=False)
    user = uuid7()

    response = await redeem(progression, user, code)

    assert_problem(response, 404, "progression.promo_invalid")
    same_problem(response, await redeem(progression, user, "ZZZZ-ZZZZ-ZZZZ"))
    assert await progression.rows("inventory", user) == []
    (row,) = await progression.rows("promo_codes")
    assert row.activations == 0
    assert await progression.rows("promo_redemptions") == []


async def test_owned_item_reward_answers_exactly_like_an_invalid_code(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    item = await owned_item(progression, user, "promo_item")
    code = await make_batch(progression, item)

    clash = await redeem(progression, user, code)
    wrong = await redeem(progression, user, "ZZZZ-ZZZZ-ZZZZ")

    assert_problem(clash, 404, "progression.promo_invalid")
    same_problem(clash, wrong)
    (row,) = await progression.rows("promo_codes")
    assert row.activations == 0  # код не потрачен
    assert await progression.outbox("progression.PromoRedeemed") == []


# ------------------------------------------------------------------ загрузка картинки
async def admin_item(svc: Service) -> str:
    token = svc.issuer.issue(
        user_id=UUID(int=82), perms=("admin.access", "progression.items.manage")
    )
    created = await svc.client.post(
        "/admin/v1/items",
        json={
            "key": "frame_gold",
            "slot": "frame",
            "rarity": "rare",
            "title": "Рамка",
            "media_kind": "static",
            "price": 30,
            **REASON,
        },
        headers={"Authorization": f"Bearer {token}", **idem()},
    )
    assert created.status_code == 201
    item_id: str = created.json()["id"]
    return item_id


def png_noise(side: int) -> bytes:
    import io
    import os

    from PIL import Image

    out = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(out, format="PNG")
    return out.getvalue()


async def test_413_from_admin_upload_is_not_cacheable(progression: Service) -> None:
    await progression.publish_config()
    item_id = await admin_item(progression)
    token = progression.issuer.issue(
        user_id=UUID(int=82), perms=("admin.access", "progression.items.manage")
    )

    response = await progression.client.put(
        f"/admin/v1/items/{item_id}/media",
        data=REASON,
        files={"file": ("a.png", png_noise(512), "image/png")},
        headers={"Authorization": f"Bearer {token}", **idem()},
    )

    assert_problem(response, 413, "progression.item_media_too_large")
    assert response.headers["cache-control"] == "no-store"


async def test_media_upload_reads_the_item_for_update(
    progression: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    await progression.publish_config()
    item_id = await admin_item(progression)
    token = progression.issuer.issue(
        user_id=UUID(int=82), perms=("admin.access", "progression.items.manage")
    )
    calls: list[bool] = []
    original = SqlItems.get

    async def spy(self: SqlItems, item: UUID, *, for_update: bool = False) -> Any:
        calls.append(for_update)
        return await original(self, item, for_update=for_update)

    monkeypatch.setattr(SqlItems, "get", spy)

    await progression.client.put(
        f"/admin/v1/items/{item_id}/media",
        data=REASON,
        files={"file": ("a.webp", b"RIFF", "image/webp")},
        headers={"Authorization": f"Bearer {token}", **idem()},
    )

    assert calls == [True]
