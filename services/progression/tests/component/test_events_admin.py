"""Admin API 5d: определения, карта, запуски, публикация, остановка, предметы событий."""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any

import pytest
from PIL import Image
from progression_service.infrastructure.db.tables_admin import admin_actions
from sqlalchemy import select

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
REASON = {"reason": "проверка события"}


def body_of(progression: Service) -> dict[str, Any]:
    """Определение из обычной находки и финала."""
    ev = progression.ev
    return ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True))


def png(size: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (200, 120, 20)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_create_event_returns_hash_and_audits(progression: Service) -> None:
    await progression.publish_config()
    response = await progression.create_event("autumn", body_of(progression))
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["key"] == "autumn" and len(data["body_sha256"]) == 64 and data["runs"] == []
    assert data["body"]["reward"] == {"item_key": None}
    assert data["map_url"] is None
    assert await progression.scalar(select(admin_actions.c.action).limit(1)) == "event.create"


async def test_create_event_is_idempotent_by_key(progression: Service) -> None:
    await progression.publish_config()
    headers = progression.admin(progression.ev.MANAGE)
    body = {"key": "autumn", "body": body_of(progression), **REASON}
    first = await progression.client.post("/admin/v1/events", headers=headers, json=body)
    again = await progression.client.post("/admin/v1/events", headers=headers, json=body)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()


async def test_duplicate_key_is_409(progression: Service) -> None:
    await progression.publish_config()
    await progression.create_event("autumn", body_of(progression))
    assert_problem(
        await progression.create_event("autumn", body_of(progression)),
        409,
        "progression.event_key_taken",
    )


async def test_definition_rules_are_reported_with_paths(progression: Service) -> None:
    await progression.publish_config()
    bad = progression.ev.body(
        progression.ev.find("a", 1),
        progression.ev.find("a", 1),
        progression.ev.find("fin", 2, final=True),
        item_key="nope",
    )
    response = await progression.create_event("autumn", bad)
    assert_problem(response, 422, "progression.event_definition_invalid")
    paths = {e["path"] for e in response.json()["details"]["errors"]}
    assert {"finds[].key", "finds[].number", "reward.item_key"} <= paths


async def test_unknown_slot_in_item_equipped_trigger_is_422(progression: Service) -> None:
    await progression.publish_config()
    body = progression.ev.body(
        progression.ev.find(
            "a", 1, progression.ev.trigger("item_equipped", 1, slot="no_such_slot")
        ),
        progression.ev.find("fin", 2, final=True),
    )
    assert_problem(
        await progression.create_event("autumn", body), 422, "progression.event_definition_invalid"
    )


async def test_reward_item_must_be_active_item_of_this_event(progression: Service) -> None:
    await progression.publish_config()
    await progression.add_item("plain_frame", price=10)
    await progression.add_item("other_frame", event_key="other_event")
    body = progression.ev.body(progression.ev.find("a", 1), progression.ev.find("f", 2, final=True))
    for item_key in ("plain_frame", "other_frame"):
        response = await progression.create_event(
            "autumn", {**body, "reward": {"item_key": item_key}}
        )
        assert_problem(response, 422, "progression.event_definition_invalid")
    await progression.add_item("autumn_frame", event_key="autumn")
    ok = await progression.create_event("autumn", {**body, "reward": {"item_key": "autumn_frame"}})
    assert ok.status_code == 201


async def test_publish_snapshot_hash_overlap_and_state_rules(progression: Service) -> None:
    await progression.publish_config()
    run = await progression.publish_event("autumn", body_of(progression))
    event = (
        await progression.client.get(
            "/admin/v1/events/autumn", headers=progression.admin(progression.ev.MANAGE)
        )
    ).json()
    assert event["runs"][0]["status"] == "active" and event["runs"][0]["seq"] == 1

    # повторная публикация: запуск уже не черновик
    again = await progression.client.post(
        f"/admin/v1/events/autumn/runs/{run}/publish",
        headers=progression.admin(progression.ev.PUBLISH),
        json={"body_sha256": event["body_sha256"], **REASON},
    )
    assert_problem(again, 409, "progression.event_run_state_invalid")

    # определение заблокировано, пока запуск идёт
    locked = await progression.client.put(
        "/admin/v1/events/autumn",
        headers=progression.admin(progression.ev.MANAGE),
        json={"body": body_of(progression), **REASON},
    )
    assert_problem(locked, 409, "progression.event_definition_locked")

    # второй запуск пересекается по времени
    await progression.create_event("winter", body_of(progression))
    second = await progression.client.post(
        "/admin/v1/events/winter/runs",
        headers=progression.admin(progression.ev.MANAGE),
        json={
            "starts_at": progression.clock.now().isoformat(),
            "ends_at": (progression.clock.now() + timedelta(days=2)).isoformat(),
            **REASON,
        },
    )
    assert second.status_code == 201
    winter = (
        await progression.client.get(
            "/admin/v1/events/winter", headers=progression.admin(progression.ev.MANAGE)
        )
    ).json()
    overlap = await progression.client.post(
        f"/admin/v1/events/winter/runs/{second.json()['id']}/publish",
        headers=progression.admin(progression.ev.PUBLISH),
        json={"body_sha256": winter["body_sha256"], **REASON},
    )
    assert_problem(overlap, 409, "progression.event_period_overlap")


async def test_publish_with_stale_hash_is_409(progression: Service) -> None:
    await progression.publish_config()
    await progression.create_event("autumn", body_of(progression))
    run = await progression.client.post(
        "/admin/v1/events/autumn/runs",
        headers=progression.admin(progression.ev.MANAGE),
        json={
            "starts_at": progression.clock.now().isoformat(),
            "ends_at": (progression.clock.now() + timedelta(days=1)).isoformat(),
            **REASON,
        },
    )
    response = await progression.client.post(
        f"/admin/v1/events/autumn/runs/{run.json()['id']}/publish",
        headers=progression.admin(progression.ev.PUBLISH),
        json={"body_sha256": "0" * 64, **REASON},
    )
    assert_problem(response, 409, "progression.event_body_changed")


async def test_period_must_be_ordered_and_in_the_future(progression: Service) -> None:
    await progression.publish_config()
    await progression.create_event("autumn", body_of(progression))
    now = progression.clock.now()
    for starts, ends in (
        (now + timedelta(days=2), now + timedelta(days=1)),
        (now - timedelta(days=3), now - timedelta(days=1)),
    ):
        response = await progression.client.post(
            "/admin/v1/events/autumn/runs",
            headers=progression.admin(progression.ev.MANAGE),
            json={"starts_at": starts.isoformat(), "ends_at": ends.isoformat(), **REASON},
        )
        assert_problem(response, 422, "progression.event_period_invalid")


async def test_stop_marks_stopped_and_publishes_finished_immediately(progression: Service) -> None:
    await progression.publish_config()
    run = await progression.publish_event("autumn", body_of(progression))
    stop = await progression.client.post(
        f"/admin/v1/events/autumn/runs/{run}/stop",
        headers=progression.admin(progression.ev.PUBLISH),
        json=REASON,
    )
    assert stop.status_code == 200 and stop.json()["status"] == "stopped"
    finished = await progression.outbox("progression.EventRunFinished")
    assert [e.payload["reason"] for e in finished] == ["stopped"]
    again = await progression.client.post(
        f"/admin/v1/events/autumn/runs/{run}/stop",
        headers=progression.admin(progression.ev.PUBLISH),
        json=REASON,
    )
    assert_problem(again, 409, "progression.event_run_state_invalid")
    # остановленный запуск не держит определение, перезапуск - новый запуск (seq 2)
    again_run = await progression.publish_event(
        "autumn", body_of(progression), create=False, starts_at=progression.clock.now()
    )
    assert again_run != run
    event = (
        await progression.client.get(
            "/admin/v1/events/autumn", headers=progression.admin(progression.ev.MANAGE)
        )
    ).json()
    assert [r["seq"] for r in event["runs"]] == [1, 2]


async def test_snapshot_survives_definition_change_after_run_ends(progression: Service) -> None:
    await progression.publish_config()
    run = await progression.publish_event("autumn", body_of(progression))
    await progression.client.post(
        f"/admin/v1/events/autumn/runs/{run}/stop",
        headers=progression.admin(progression.ev.PUBLISH),
        json=REASON,
    )
    changed = progression.ev.body(
        progression.ev.find("only", 1), progression.ev.find("fin", 2, final=True)
    )
    update = await progression.client.put(
        "/admin/v1/events/autumn",
        headers=progression.admin(progression.ev.MANAGE),
        json={"body": changed, **REASON},
    )
    assert update.status_code == 200
    rows = await progression.rows("event_runs")
    assert [f["key"] for f in rows[0].snapshot["finds"]] == ["leaf", "fin"]


async def test_map_upload_validates_and_replaces(progression: Service) -> None:
    await progression.publish_config()
    await progression.create_event("autumn", body_of(progression))
    path = "/admin/v1/events/autumn/map"
    bad = await progression.client.put(
        path,
        headers=progression.admin(progression.ev.MANAGE),
        data=REASON,
        files={"file": ("m.png", b"not an image", "image/png")},
    )
    assert_problem(bad, 422, "progression.event_map_invalid")
    ok = await progression.client.put(
        path,
        headers=progression.admin(progression.ev.MANAGE),
        data=REASON,
        files={"file": ("m.png", png(), "image/png")},
    )
    assert ok.status_code == 200 and ok.json()["map_url"].endswith(".png")
    first = ok.json()["map_url"]
    second = await progression.client.put(
        path,
        headers=progression.admin(progression.ev.MANAGE),
        data=REASON,
        files={"file": ("m.png", png(12), "image/png")},
    )
    assert second.json()["map_url"] != first


async def test_unknown_event_and_run_are_404(progression: Service) -> None:
    await progression.publish_config()
    headers = progression.admin(progression.ev.MANAGE)
    assert_problem(
        await progression.client.get("/admin/v1/events/nope", headers=headers),
        404,
        "progression.event_not_found",
    )
    await progression.create_event("autumn", body_of(progression))
    stats = await progression.client.get(
        f"/admin/v1/events/autumn/runs/{uuid7()}/stats", headers=headers
    )
    assert_problem(stats, 404, "progression.event_run_not_found")


async def test_list_shows_latest_run_status(progression: Service) -> None:
    await progression.publish_config()
    await progression.create_event("draft_only", body_of(progression))
    await progression.publish_event("autumn", body_of(progression))
    items = (
        await progression.client.get(
            "/admin/v1/events", headers=progression.admin(progression.ev.MANAGE)
        )
    ).json()["items"]
    by_key = {i["key"]: i for i in items}
    assert by_key["draft_only"]["status"] is None and by_key["draft_only"]["latest_run"] is None
    assert by_key["autumn"]["status"] == "active"


@pytest.mark.parametrize("perm", ["progression.events.manage", None])
async def test_publish_and_stop_need_publish_permission(
    progression: Service, perm: str | None
) -> None:
    await progression.publish_config()
    headers = progression.admin(*([perm] if perm else []))
    for suffix, payload in (("publish", {"body_sha256": "0" * 64, **REASON}), ("stop", REASON)):
        response = await progression.client.post(
            f"/admin/v1/events/autumn/runs/{uuid7()}/{suffix}", headers=headers, json=payload
        )
        assert_problem(response, 403, "auth.forbidden")
    anonymous = await progression.client.get("/admin/v1/events")
    assert_problem(anonymous, 401, "auth.unauthenticated")


# ------------------------------------------------------------------ предмет только для события
ITEM = {
    "key": "winter_frame",
    "slot": "frame",
    "rarity": "rare",
    "title": "Зимняя рамка",
    "media_kind": "static",
    "event_key": "winter",
    **REASON,
}


async def test_event_item_cannot_have_price_or_level(progression: Service) -> None:
    await progression.publish_config()
    headers = progression.admin("progression.items.manage")
    for extra in ({"price": 10}, {"level_required": 3}):
        response = await progression.client.post(
            "/admin/v1/items", headers=headers, json={**ITEM, **extra}
        )
        assert_problem(response, 422, "progression.item_event_exclusive")
    ok = await progression.client.post("/admin/v1/items", headers=headers, json=ITEM)
    assert ok.status_code == 201 and ok.json()["event_key"] == "winter"


async def test_event_item_is_not_on_storefront_wheel_promo_or_pack(progression: Service) -> None:
    await progression.publish_config()
    item = await progression.add_item("winter_frame", event_key="winter")
    storefront = await progression.client.get("/v1/shop/items", headers=progression.user())
    assert storefront.status_code == 200
    assert "winter_frame" not in storefront.text

    admin = progression.admin("progression.promo.manage", "progression.items.manage")
    promo = await progression.client.post(
        "/admin/v1/promo-codes",
        headers=admin,
        json={
            "label": "x",
            "reward": {"kind": "item", "item_id": str(item)},
            "count": 1,
            "max_uses": 1,
            **REASON,
        },
    )
    assert promo.status_code == 422
    pack = await progression.client.post(
        "/admin/v1/packs",
        headers=admin,
        json={
            "key": "p",
            "title": "p",
            "style": "winter",
            "item_ids": [str(item)],
            "price_kopecks": 100,
            **REASON,
        },
    )
    assert pack.status_code == 422
