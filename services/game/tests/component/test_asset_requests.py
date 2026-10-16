"""Запрос ассетов под загадку черновика (D8, ADR 0025), предупреждение `assets.profile_missing`."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from gv_core.ids import uuid7
from gv_game_kernel import params_hash
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

MODE = "film.random.description"
BLACKOUT = "film.random.blackout"
VIEW, EDIT, PUBLISH = "config.modes.view", "config.modes.edit", "config.modes.publish"


def seed(key: str) -> dict[str, Any]:
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes" / f"{key}.yaml"
    body: dict[str, Any] = yaml.safe_load(path.read_text("utf-8"))
    return body


def with_key(headers: dict[str, str], key: str | None = None) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key or f"k-{uuid7().hex}"}


def new_levels() -> dict[str, Any]:
    body = copy.deepcopy(seed(MODE))
    body["clue"]["params"]["levels"] = body["clue"]["params"]["levels"][:5]
    body["scoring"]["params"]["points"] = [5, 4, 3, 2, 1]
    return body


async def save_draft(game: Any, key: str, body: dict[str, Any]) -> dict[str, Any]:
    r = await game.client.put(
        f"/admin/v1/mode-configs/{key}/draft",
        json={"body": body, "base_revision": None, "reason": "новые уровни описания"},
        headers=with_key(game.admin(EDIT)),
    )
    assert r.status_code == 200, r.text
    draft: dict[str, Any] = r.json()
    return draft


async def request(game: Any, key: str, revision: int, idem: str | None = None) -> Any:
    return await game.client.post(
        f"/admin/v1/mode-configs/{key}/asset-requests",
        json={"draft_revision": revision, "reason": "нужны ассеты новых уровней"},
        headers=with_key(game.admin(PUBLISH), idem),
    )


async def test_request_assets_publishes_event_once_until_assets_arrive(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    body = new_levels()
    digest = params_hash(body["clue"]["params"])
    draft = await save_draft(game, MODE, body)
    validation = draft["validation"]
    assert {"code": "assets.profile_missing", "path": "clue.params"} in validation["warnings"]
    assert validation["pool"] == {"playable_count": 0}

    idem = f"k-{uuid7().hex}"
    first = await request(game, MODE, draft["revision"], idem)
    assert first.status_code == 201, first.text
    out = first.json()
    assert (out["status"], out["params_hash"], out["clue_type"]) == (
        "pending",
        digest,
        "description",
    )
    assert out["draft_revision"] == draft["revision"]
    replay = await request(game, MODE, draft["revision"], idem)
    assert replay.json() == out
    repeat = await request(game, MODE, draft["revision"])  # новый ключ, тот же хэш
    assert repeat.status_code == 201 and repeat.json()["request_id"] == out["request_id"]

    events = [e for e in await game.outbox() if e.event_type == "game.ClueAssetsRequested"]
    assert len(events) == 1
    (event,) = events
    assert_event_matches_schema(event)
    assert event.aggregate_id == MODE
    assert event.payload["params"] == body["clue"]["params"]
    assert event.payload["params_hash"] == digest
    assert event.payload["request_id"] == out["request_id"]

    listed = await game.client.get(
        f"/admin/v1/mode-configs/{MODE}/asset-requests", headers=game.admin(VIEW)
    )
    assert [r["request_id"] for r in listed.json()] == [out["request_id"]]

    item = await game.add_item(domain="film")
    await game.ready_assets(item, MODE, body=body)
    listed = await game.client.get(
        f"/admin/v1/mode-configs/{MODE}/asset-requests", headers=game.admin(VIEW)
    )
    assert listed.json()[0]["status"] == "sent"
    already = await request(game, MODE, draft["revision"])
    assert_problem(already, 409, "game.assets_already_available")


async def test_request_assets_for_missing_reveal_and_bad_input(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    item = await game.add_item(domain="film", ready_for=BLACKOUT)  # звук есть, кадра нет
    assert item
    draft = await save_draft(game, BLACKOUT, seed(BLACKOUT))
    warnings = draft["validation"]["warnings"]
    assert {"code": "assets.profile_missing", "path": "clue.reveal.params"} in warnings
    assert {"code": "assets.profile_missing", "path": "clue.params"} not in warnings
    r = await request(game, BLACKOUT, draft["revision"])
    assert r.status_code == 201, r.text
    assert r.json()["clue_type"] == "frame_progressive"

    assert_problem(await request(game, BLACKOUT, draft["revision"] + 1), 409, "game.draft_conflict")
    broken = copy.deepcopy(seed(MODE))
    broken["clue"]["params"]["levels"] = [{"fields": ["no_such_field"]}]
    bad = await save_draft(game, MODE, broken)
    assert_problem(await request(game, MODE, bad["revision"]), 422, "game.config_invalid")
    missing = await request(game, "film.random.nothing", 1)
    assert_problem(missing, 404, "game.draft_not_found")
