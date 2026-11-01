"""Конфиги режимов и наборы дня через admin-API; случайный режим на новой версии."""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import httpx
import pytest
import yaml

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem, assert_published

pytestmark = pytest.mark.component

RANDOM = "music.random.classic"
DAILY = "music.daily.classic"
VIEW, EDIT, PUBLISH = "config.modes.view", "config.modes.edit", "config.modes.publish"
DAILY_VIEW, CURATE = "game.daily.view", "game.daily.curate"
TOMORROW = "2026-10-11"


def seed(key: str) -> dict[str, Any]:
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes" / f"{key}.yaml"
    body: dict[str, Any] = yaml.safe_load(path.read_text("utf-8"))
    return body


def with_key(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": f"k-{uuid7().hex}"}


async def ready(game: Any, count: int = 25) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    for i in range(count):
        item = await game.add_item(year=2000 + i % 20, ready_for=DAILY)
        assert item
    assert (await game.run_job("game.daily.schedule")).value == "ok"


async def start(game: Any, token: str, mode: str, **extra: Any) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        "/v1/sessions",
        json={"mode_key": mode, **extra},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"k-{uuid7().hex}"},
    )
    return response


async def test_random_mode_with_preset(game: Any) -> None:
    await ready(game)
    token = game.issuer.issue_guest()
    r = await start(game, token, RANDOM, preset_id="y2000s")
    assert r.status_code == 201, r.text
    snap = r.json()
    assert snap["day"] is None and len(snap["rounds"]) == 5
    assert_problem(await start(game, token, RANDOM, preset_id="nope"), 422, "game.preset_not_found")
    assert_problem(await start(game, token, "music.unknown.mode"), 404, "game.mode_not_found")
    modes = (await game.client.get("/v1/modes")).json()
    presets = next(m for m in modes if m["key"] == RANDOM)["presets"]
    assert {p["id"] for p in presets} >= {"y1980s", "y2000s", "hip_hop", "rock"}


async def test_random_pool_empty(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    r = await start(game, game.issuer.issue(), RANDOM)
    assert_problem(r, 503, "game.pool_empty")


async def test_draft_validation_paths_and_permissions(game: Any) -> None:
    await ready(game)
    broken = copy.deepcopy(seed(RANDOM))
    broken["session"]["rounds"] = 0
    del broken["scoring"]["params"]["points"]
    r = await game.client.put(
        f"/admin/v1/mode-configs/{RANDOM}/draft",
        json={"body": broken, "base_revision": None, "reason": "сломанный черновик"},
        headers=with_key(game.admin(EDIT)),
    )
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["revision"] == 1 and draft["validation"]["ok"] is False
    paths = {e["path"] for e in draft["validation"]["errors"]}
    assert "session.rounds" in paths
    assert all(
        e["message_key"].startswith("admin.mode_config.errors.")
        for e in draft["validation"]["errors"]
    )

    publish = {"draft_revision": 1, "reason": "попытка"}
    r = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/publish", json=publish, headers=with_key(game.admin(EDIT))
    )
    assert_problem(r, 403, "auth.forbidden")
    r = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/publish",
        json=publish,
        headers=with_key(game.admin(PUBLISH)),
    )
    assert_problem(r, 422, "game.config_invalid")

    stale = await game.client.put(
        f"/admin/v1/mode-configs/{RANDOM}/draft",
        json={"body": broken, "base_revision": None, "reason": "гонка"},
        headers=with_key(game.admin(EDIT)),
    )
    assert_problem(stale, 409, "game.draft_conflict")

    v = await game.client.post(
        "/admin/v1/mode-configs/validate", json={"body": seed(RANDOM)}, headers=game.admin(VIEW)
    )
    assert v.json()["ok"] is True and v.json()["pool"]["playable_count"] == 25


async def test_too_large_body_rejected(game: Any) -> None:
    body = {"key": RANDOM, "junk": "x" * 70000}
    r = await game.client.post(
        "/admin/v1/mode-configs/validate", json={"body": body}, headers=game.admin(VIEW)
    )
    assert_problem(r, 422, "game.config_too_large")


async def test_publish_v2_new_random_on_v2_daily_on_set_version(game: Any) -> None:
    await ready(game)
    body = seed(RANDOM)
    body["scoring"]["params"]["points"] = [10, 8, 6, 4, 2, 1]
    r = await game.client.put(
        f"/admin/v1/mode-configs/{RANDOM}/draft",
        json={"body": body, "base_revision": None, "reason": "очки уровней"},
        headers=with_key(game.admin(EDIT)),
    )
    assert r.json()["validation"]["ok"] is True, r.json()
    headers = with_key(game.admin(PUBLISH))
    pub = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/publish",
        json={"draft_revision": 1, "reason": "публикация v2"},
        headers=headers,
    )
    assert pub.status_code == 200, pub.text
    assert pub.json()["version"] == 2
    replay = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/publish",
        json={"draft_revision": 1, "reason": "публикация v2"},
        headers=headers,
    )
    assert replay.json() == pub.json()

    events = await game.outbox()
    published = assert_published(
        events, "game.ModeConfigPublished", {"mode_key": RANDOM, "version": 2}
    )
    assert_event_matches_schema(published)

    snap = (await start(game, game.issuer.issue(), RANDOM)).json()
    assert snap["mode"]["version"] == 2 and snap["max_score"] == 50
    daily = (await start(game, game.issuer.issue(), DAILY)).json()
    assert daily["mode"]["version"] == 1

    detail = (
        await game.client.get(f"/admin/v1/mode-configs/{RANDOM}", headers=game.admin(VIEW))
    ).json()
    assert [v["version"] for v in detail["versions"]] == [1, 2] and detail["draft"] is None

    rb = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/rollback",
        json={"to_version": 1, "reason": "вернуть очки"},
        headers=with_key(game.admin(PUBLISH)),
    )
    assert rb.json()["version"] == 3 and rb.json()["rolled_back_from"] == 1
    v1 = await game.client.get(
        f"/admin/v1/mode-configs/{RANDOM}/versions/1", headers=game.admin(VIEW)
    )
    assert v1.json()["body"] == rb.json()["body"]

    arch = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/archive",
        json={"reason": "убрать"},
        headers=with_key(game.admin(PUBLISH)),
    )
    assert arch.json()["status"] == "archived"
    keys = {m["key"] for m in (await game.client.get("/v1/modes")).json()}
    assert RANDOM not in keys
    assert_problem(await start(game, game.issuer.issue(), RANDOM), 404, "game.mode_not_found")
    archived = assert_published(
        await game.outbox(), "game.ModeConfigArchived", {"mode_key": RANDOM}
    )
    assert_event_matches_schema(archived)
    listed = (
        await game.client.get(
            "/admin/v1/mode-configs", params={"status": "archived"}, headers=game.admin(VIEW)
        )
    ).json()
    assert [m["key"] for m in listed] == [RANDOM]


async def test_daily_set_curation(game: Any) -> None:
    await ready(game)
    sets = await game.client.get(
        "/admin/v1/daily-sets",
        params={"mode_key": DAILY, "from": "2026-10-10", "to": "2026-10-12"},
        headers=game.admin(DAILY_VIEW),
    )
    assert sets.status_code == 200, sets.text
    by_day = {s["day"]: s for s in sets.json()}
    assert by_day["2026-10-10"]["status"] == "locked"
    assert by_day[TOMORROW]["status"] == "scheduled"
    assert all(i["playable"] for i in by_day[TOMORROW]["items"])

    used = {i["media_item_id"] for s in sets.json() for i in s["items"]}
    candidates = (
        await game.client.get(
            "/admin/v1/projection/items",
            params={"playable_for_mode": DAILY, "limit": 50},
            headers=game.admin(DAILY_VIEW),
        )
    ).json()
    fresh = next(c for c in candidates if c["media_item_id"] not in used and c["playable"])
    path = f"/admin/v1/daily-sets/{DAILY}/{TOMORROW}/items/0"
    ok = await game.client.put(
        path,
        json={"media_item_id": fresh["media_item_id"], "reason": "надпись"},
        headers=with_key(game.admin(CURATE)),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["items"][0]["media_item_id"] == fresh["media_item_id"]

    dup = by_day[TOMORROW]["items"][1]["media_item_id"]
    r = await game.client.put(
        path, json={"media_item_id": dup, "reason": "дубль"}, headers=with_key(game.admin(CURATE))
    )
    assert_problem(r, 409, "game.item_duplicate")
    unplayable = await game.add_item()
    r = await game.client.put(
        path,
        json={"media_item_id": str(unplayable), "reason": "без ассетов"},
        headers=with_key(game.admin(CURATE)),
    )
    assert_problem(r, 409, "game.item_not_playable")
    today = f"/admin/v1/daily-sets/{DAILY}/2026-10-10/items/0"
    r = await game.client.put(
        today,
        json={"media_item_id": fresh["media_item_id"], "reason": "поздно"},
        headers=with_key(game.admin(CURATE)),
    )
    assert_problem(r, 409, "game.daily_locked")
    r = await game.client.put(
        f"/admin/v1/daily-sets/{DAILY}/{TOMORROW}/items/9",
        json={"media_item_id": fresh["media_item_id"], "reason": "вне набора"},
        headers=with_key(game.admin(CURATE)),
    )
    assert_problem(r, 422, "game.daily_index_out_of_range")

    regen = await game.client.post(
        f"/admin/v1/daily-sets/{DAILY}/{TOMORROW}/regenerate",
        json={"reason": "заново"},
        headers=with_key(game.admin(CURATE)),
    )
    assert regen.status_code == 200, regen.text
    assert regen.json()["status"] == "scheduled"

    from game_service.infrastructure.db.tables import admin_actions
    from sqlalchemy import select

    async with game.infra.sessions() as s:
        actions = [r for (r,) in await s.execute(select(admin_actions.c.action))]
    assert actions.count("daily_set.replace_item") == 1
    assert "daily_set.regenerate" in actions


async def test_blocked_item_replaced_before_lock(game: Any) -> None:
    await ready(game)
    sets = (
        await game.client.get(
            "/admin/v1/daily-sets",
            params={"mode_key": DAILY, "from": TOMORROW, "to": TOMORROW},
            headers=game.admin(DAILY_VIEW),
        )
    ).json()
    victim = sets[0]["items"][2]["media_item_id"]
    from gv_testkit import load_example

    blocked = load_example("catalog.MediaItemBlocked")
    blocked = blocked.model_copy(
        update={"payload": {**blocked.payload, "media_item_id": victim, "version": 5}}
    )
    assert (await game.deliver(blocked)).value == "processed"
    game.clock.advance(timedelta(days=1))
    assert (await game.run_job("game.daily.schedule")).value == "ok"
    locked = (
        await game.client.get(
            "/admin/v1/daily-sets",
            params={"mode_key": DAILY, "from": TOMORROW, "to": TOMORROW},
            headers=game.admin(DAILY_VIEW),
        )
    ).json()[0]
    assert locked["status"] == "locked"
    assert victim not in {i["media_item_id"] for i in locked["items"]}
    assert all(i["playable"] for i in locked["items"])


async def test_projection_ignores_stale_events(game: Any) -> None:
    item = await game.add_item(title="Новое имя", version=3)
    from gv_testkit import load_example

    old = load_example("catalog.MediaItemUpdated")
    old = old.model_copy(
        update={
            "payload": {
                **old.payload,
                "media_item_id": str(item),
                "version": 2,
                "title": "Старое имя",
            }
        }
    )
    assert (await game.deliver(old)).value == "processed"
    found = (
        await game.client.get(
            "/admin/v1/projection/items", params={"q": "имя"}, headers=game.admin(DAILY_VIEW)
        )
    ).json()
    assert [f["title"] for f in found] == ["Новое имя"]

    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    asset_set = await game.ready_assets(item, DAILY, version=2)

    async def playable() -> bool | None:
        found = (
            await game.client.get(
                "/admin/v1/projection/items",
                params={"q": "имя", "playable_for_mode": DAILY},
                headers=game.admin(DAILY_VIEW),
            )
        ).json()
        return found[0]["playable"]  # type: ignore[no-any-return]

    assert await playable() is True
    failed = load_example("media_pipeline.AssetsFailed")
    stale = failed.model_copy(
        update={
            "payload": {
                **failed.payload,
                "media_item_id": str(item),
                "asset_set_id": str(asset_set),
                "version": 1,
            }
        }
    )
    assert (await game.deliver(stale)).value == "processed"
    assert await playable() is True  # устаревшее событие не применяется
    fresh = load_example("media_pipeline.AssetsFailed").model_copy(
        update={
            "payload": {
                **failed.payload,
                "media_item_id": str(item),
                "asset_set_id": str(asset_set),
                "version": 3,
            }
        }
    )
    assert (await game.deliver(fresh)).value == "processed"
    assert await playable() is False


async def test_config_cache_generation_invalidates_other_process(game: Any) -> None:
    from game_service.infrastructure.config_cache import CachedModeConfigs
    from game_service.infrastructure.container import container_for

    await ready(game)
    container = container_for(game.infra)
    ticks = [0.0]
    other = CachedModeConfigs(
        container.uow_factory(), game.infra.redis, ttl_s=3600, monotonic=lambda: ticks[0]
    )
    assert (await other.latest(RANDOM)).version == 1  # type: ignore[union-attr]
    body = seed(RANDOM)
    body["session"]["rounds"] = 4
    await game.client.put(
        f"/admin/v1/mode-configs/{RANDOM}/draft",
        json={"body": body, "base_revision": None, "reason": "раунды"},
        headers=with_key(game.admin(EDIT)),
    )
    published = await game.client.post(
        f"/admin/v1/mode-configs/{RANDOM}/publish",
        json={"draft_revision": 1, "reason": "публикация"},
        headers=with_key(game.admin(PUBLISH)),
    )
    assert published.status_code == 200, published.text
    assert (await other.latest(RANDOM)).version == 1  # type: ignore[union-attr]  # поколение сверяется раз в секунду
    ticks[0] += 2
    assert (await other.latest(RANDOM)).version == 2  # type: ignore[union-attr]
