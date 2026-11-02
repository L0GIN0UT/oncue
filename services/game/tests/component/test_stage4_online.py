"""Онлайн-режимы в game (план этапа 4, §6 B1–B2; ADR 0032, 0033) на реальных плагинах ядра.

Проверяется то, что не зависит от плагинов состязания потока A: публикация онлайн-шаблонов
(дуэли с ``by_level``), выдача в ``GET /v1/modes?session=online`` и только там, отказ соло-
старта онлайн-режима до побочных эффектов, план раундов для match (детерминизм по ``seed``,
ключи ответа, ассеты уровней, ``exclude_ids``, версии, сборники и ошибки) и отсутствие ключей
ответа в логах. Сценарии на плагинах A (party, лобби с ``party_speed``, несколько сборников) —
``test_stage4_online_kernel.py``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from game_service.infrastructure.container import container_for
from game_service.infrastructure.db.tables import game_sessions
from sqlalchemy import func, select
from structlog.testing import capture_logs

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_problem, load_example

pytestmark = pytest.mark.component

DUEL = "film.duel.frame"
MUSIC_DUEL = "music.duel.classic"
SOLO = "film.random.frame"
SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"
COLLECTION_LOBBY = "film.lobby.coltest"
"""Лобби по сборнику на ``by_level``: не ждёт ``party_speed`` потока A."""
FRAME_ASSETS = "film.collection.frame"
"""Сид с теми же ``clue.params``, что у лобби по сборнику: по нему готовятся ассеты."""


def seed(key: str) -> dict[str, Any]:
    body: dict[str, Any] = yaml.safe_load((SEEDS_DIR / f"{key}.yaml").read_text("utf-8"))
    return body


def collection_lobby_body() -> dict[str, Any]:
    body = copy.deepcopy(seed("film.lobby.collection"))
    body["key"] = COLLECTION_LOBBY
    body["scoring"] = {
        "type": "by_level",
        "params": {"points": [6, 5, 4, 3, 2, 1], "partial_ratio": 0.5},
    }
    return body


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def match_headers(service_token: Callable[..., str]) -> dict[str, str]:
    return bearer(service_token("match", "game", ["rounds.plan"]))


async def bootstrap(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"


async def films(game: Any, n: int, mode: str = DUEL) -> list[UUID]:
    return [await game.add_item(domain="film", ready_for=mode) for _ in range(n)]


async def plan(game: Any, headers: dict[str, str], **body: Any) -> Any:
    payload = {"mode_key": DUEL, "seed": "match-0000000001", "rounds": 5, **body}
    payload = {k: v for k, v in payload.items() if v is not None}
    return await game.client.post("/internal/v1/round-plans", json=payload, headers=headers)


def targets(response: Any) -> list[str]:
    return [r["answer"]["media_item_id"] for r in response.json()["rounds"]]


# ============================================================== B1: каталог и соло-старт
async def test_online_modes_listed_only_with_session_online(game: Any) -> None:
    await bootstrap(game)

    solo = (await game.client.get("/v1/modes")).json()
    templates = (await game.client.get("/v1/modes", params={"kind": "collection"})).json()
    online = (await game.client.get("/v1/modes", params={"session": "online"})).json()

    online_keys = {m["key"] for m in online}
    assert {DUEL, MUSIC_DUEL} <= online_keys
    assert online_keys.isdisjoint({m["key"] for m in solo})
    assert online_keys.isdisjoint({m["key"] for m in templates})
    assert all(m["online"] is None for m in solo + templates)
    duel = next(m for m in online if m["key"] == MUSIC_DUEL)
    assert duel["allow_guest"] is False and duel["presets"] is None
    assert duel["rounds"] == 7 and len(duel["levels"]) == 2
    assert duel["online"] == {
        "kind": "duel",
        "players": {"min": 2, "max": 2},
        "rounds": 7,
        "level_windows_s": [25, 40],
        "contest_type": "race",
        "ranked": False,
        "ladder": None,
        "max_collections": None,
        "lobby": None,
        "chat": {
            "emotes": [
                "match.emotes.wow",
                "match.emotes.gg",
                "match.emotes.fire",
                "match.emotes.laugh",
                "match.emotes.think",
            ],
            "phrases": [
                "match.phrases.good_luck",
                "match.phrases.well_played",
                "match.phrases.nice_one",
                "match.phrases.so_close",
            ],
            "free_text": False,
        },
    }
    film_only = (
        await game.client.get("/v1/modes", params={"session": "online", "domain": "film"})
    ).json()
    assert {m["domain"] for m in film_only} == {"film"}
    assert (await game.client.get("/v1/modes", params={"session": "duel"})).status_code == 422


async def test_admin_validates_and_publishes_online_configs(game: Any) -> None:
    """Онлайн-конфиги проверяются и публикуются тем же путём, что соло (D2)."""
    view = game.admin("config.modes.view")
    ok = await game.client.post(
        "/admin/v1/mode-configs/validate", json={"body": seed(DUEL)}, headers=view
    )
    # Пул пуст (контент не загружен) — единственная проблема; сессия и плагины валидны.
    assert ok.status_code == 200, ok.text
    assert [e["code"] for e in ok.json()["errors"]] == ["config.pool_too_small"]

    body = seed(DUEL)
    body["session"]["allow_guest"] = True  # у онлайн-сессии поля нет (ADR 0036)
    broken = await game.client.post(
        "/admin/v1/mode-configs/validate", json={"body": body}, headers=view
    )
    assert broken.json()["ok"] is False
    assert [(e["path"], e["code"]) for e in broken.json()["errors"]] == [
        ("session.allow_guest", "schema.extra_forbidden")
    ]

    await bootstrap(game)
    detail = await game.client.get(f"/admin/v1/mode-configs/{DUEL}", headers=view)
    assert detail.json()["published_version"] == 1


async def test_admin_list_has_session_kind_and_filter(game: Any) -> None:
    """Админка (поток E): вид сессии в списке режимов и фильтр ``session``."""
    await bootstrap(game)
    view = game.admin("config.modes.view")
    # Неопубликованный онлайн-режим — по черновику.
    draft = seed(DUEL)
    draft["key"] = "film.duel.draftonly"
    saved = await game.client.put(
        "/admin/v1/mode-configs/film.duel.draftonly/draft",
        json={"body": draft, "base_revision": None, "reason": "черновик дуэли"},
        headers={**game.admin("config.modes.edit"), "Idempotency-Key": f"k-{uuid7().hex}"},
    )
    assert saved.status_code == 200, saved.text

    rows = (await game.client.get("/admin/v1/mode-configs", headers=view)).json()
    kinds = {r["key"]: r["session_kind"] for r in rows}
    assert kinds[DUEL] == "online" and kinds[MUSIC_DUEL] == "online"
    assert kinds["film.duel.draftonly"] == "online" and kinds[SOLO] == "solo"

    online = (
        await game.client.get("/admin/v1/mode-configs", params={"session": "online"}, headers=view)
    ).json()
    solo = (
        await game.client.get("/admin/v1/mode-configs", params={"session": "solo"}, headers=view)
    ).json()
    assert {r["session_kind"] for r in online} == {"online"}
    assert {r["session_kind"] for r in solo} == {"solo"}
    assert {r["key"] for r in online} | {r["key"] for r in solo} == set(kinds)
    bad = await game.client.get("/admin/v1/mode-configs", params={"session": "duel"}, headers=view)
    assert bad.status_code == 422


async def test_editor_schema_has_contest_section(game: Any) -> None:
    schema = (
        await game.client.get(
            "/admin/v1/mode-configs/schema", headers=game.admin("config.modes.view")
        )
    ).json()
    contest = schema["plugins"]["contest"]
    assert set(contest) == {"parallel", "race"}
    assert "lock_on_first_correct" in contest["race"]["properties"]
    assert "end_when_all_answered" in contest["parallel"]["properties"]


async def _session_rows(game: Any) -> int:
    async with game.infra.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(game_sessions)) or 0)


async def test_solo_start_of_online_mode_is_rejected_before_side_effects(game: Any) -> None:
    await bootstrap(game)
    await films(game, 20)
    outbox_before = len(await game.outbox())
    user = game.issuer.issue()
    redis = game.infra.redis
    assert redis is not None
    limits_before = {k async for k in redis.scan_iter(match="game:rl:*")}

    for token in (user, game.issuer.issue_guest()):
        response = await game.client.post(
            "/v1/sessions",
            json={"mode_key": DUEL},
            headers={**bearer(token), "Idempotency-Key": f"k-{uuid7().hex}"},
        )
        assert_problem(response, 422, "game.mode_not_solo")
        assert response.json()["details"] == {"kind": "duel"}

    assert await _session_rows(game) == 0
    assert len(await game.outbox()) == outbox_before
    # Лимиты не тронуты: новых окон в Redis нет (Redis общий для прогонов — сравниваем).
    assert {k async for k in redis.scan_iter(match="game:rl:*")} == limits_before
    # Онлайн-режим не попадает в плановые наборы (дейлик).
    daily = (await game.client.get("/v1/daily", headers=bearer(user))).json()
    assert DUEL not in {d["mode_key"] for d in daily}


# ============================================================== B2: план раундов
async def test_round_plan_is_deterministic_and_carries_answer_keys(
    game: Any, match_headers: dict[str, str]
) -> None:
    await bootstrap(game)
    items = await films(game, 25)

    first = await plan(game, match_headers, rounds=7)
    again = await plan(game, match_headers, rounds=7)
    other = await plan(game, match_headers, rounds=7, seed="match-0000000002")

    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == again.json()
    assert targets(first) != targets(other)
    body = first.json()
    assert body["seed"] == "match-0000000001" and body["collections"] == []
    assert body["mode"] == {"key": DUEL, "version": 1, "domain": "film", "body": seed(DUEL)}
    assert [r["index"] for r in body["rounds"]] == list(range(7))
    assert len(set(targets(first))) == 7
    assert {UUID(t) for t in targets(first)} <= set(items)
    one = body["rounds"][0]
    assert one["answer"]["title"].startswith("Тестовая единица")
    assert one["answer"]["aliases"] and one["answer"]["domain"] == "film"
    assert one["answer"]["partial"] == []
    assert [lvl["level"] for lvl in one["levels"]] == [1, 2, 3, 4, 5, 6]
    assert len({lvl["asset_set_id"] for lvl in one["levels"]}) == 1
    assert one["reveal_asset_set_id"] is None and one["options"] == []


async def test_round_plan_music_partial_keys(game: Any, match_headers: dict[str, str]) -> None:
    await bootstrap(game)
    for _ in range(3):
        await game.add_item(domain="music", ready_for=MUSIC_DUEL, artist=(uuid7(), "Ария Группа"))

    response = await plan(game, match_headers, mode_key=MUSIC_DUEL, rounds=3)

    assert response.status_code == 200, response.text
    answer = response.json()["rounds"][0]["answer"]
    assert answer["partial"] == ["artist"]
    assert answer["persons"][0]["name"] == "Ария Группа"
    assert answer["persons"][0]["normalized"] == "ария группа"
    assert len(response.json()["rounds"][0]["levels"]) == 2


async def test_round_plan_respects_exclude_ids_and_pool_size(
    game: Any, match_headers: dict[str, str]
) -> None:
    await bootstrap(game)
    items = await films(game, 8)
    played = [str(i) for i in items[:5]]

    response = await plan(game, match_headers, rounds=3, exclude_ids=played)
    assert response.status_code == 200, response.text
    assert set(targets(response)) == {str(i) for i in items[5:]}

    too_many = await plan(game, match_headers, rounds=9)
    assert_problem(too_many, 422, "game.pool_too_small")
    assert too_many.json()["details"]["available"] == 8
    assert_problem(
        await plan(game, match_headers, rounds=4, exclude_ids=played), 422, "game.pool_too_small"
    )


async def test_round_plan_mode_errors(game: Any, match_headers: dict[str, str]) -> None:
    await bootstrap(game)
    await films(game, 6)

    assert (await plan(game, match_headers, version=1)).status_code == 200
    assert_problem(await plan(game, match_headers, version=9), 404, "game.mode_not_found")
    assert_problem(
        await plan(game, match_headers, mode_key="film.duel.nothing"), 404, "game.mode_not_found"
    )
    # Онлайн-план — только онлайн-режимам.
    solo = await plan(game, match_headers, mode_key=SOLO)
    assert_problem(solo, 422, "game.mode_not_online")
    assert_problem(
        await plan(game, match_headers, collection_ids=[str(uuid7())]),
        422,
        "game.collection_not_supported",
    )


async def test_mode_version_body(game: Any, match_headers: dict[str, str]) -> None:
    await bootstrap(game)

    response = await game.client.get(f"/internal/v1/modes/{DUEL}/versions/1", headers=match_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"key": DUEL, "version": 1, "domain": "film", "body": seed(DUEL)}
    assert response.headers["cache-control"] == "no-store"
    missing = await game.client.get(f"/internal/v1/modes/{DUEL}/versions/2", headers=match_headers)
    assert_problem(missing, 404, "game.mode_not_found")


async def test_round_plan_keeps_answer_keys_out_of_logs(
    game: Any, match_headers: dict[str, str]
) -> None:
    await bootstrap(game)
    await films(game, 6)

    with capture_logs() as logs:
        ok = await plan(game, match_headers, rounds=5)
        failed = await plan(game, match_headers, rounds=50)

    assert ok.status_code == 200
    assert_problem(failed, 422, "game.pool_too_small")
    secrets = set()
    for r in ok.json()["rounds"]:
        a = r["answer"]
        secrets |= {a["media_item_id"], a["title"], *a["aliases"]}
        secrets |= {lvl["asset_set_id"] for lvl in r["levels"]}
    issued = [e for e in logs if e.get("event") == "game.round_plan_issued"]
    assert len(issued) == 1 and issued[0]["caller"] == "match"
    dumped = json.dumps(logs, ensure_ascii=False, default=str)
    assert not [s for s in secrets if s in dumped]
    assert not [s for s in secrets if s in failed.text]


# ============================================================== B2: сборники
async def publish_collection(
    game: Any, items: list[UUID], *, status: str = "published", domain: str = "film"
) -> UUID:
    cid = uuid7()
    base = load_example("collections.CollectionPublished")
    payload = {
        **base.payload,
        "collection_id": str(cid),
        "version": 1,
        "content_version": 3,
        "domain": domain,
        "status": status,
        "default_mode_key": f"{domain}.collection.{'frame' if domain == 'film' else 'intro'}",
        "items": [{"media_item_id": str(i), "position": p} for p, i in enumerate(items)],
    }
    envelope = base.model_copy(update={"aggregate_id": str(cid), "payload": payload})
    assert await game.deliver(envelope) is Outcome.PROCESSED
    return cid


@pytest.fixture
async def collection_lobby(game: Any, tmp_path: Path) -> dict[str, Any]:
    """Онлайн-шаблон лобби по сборнику, опубликованный тем же bootstrap, что и сиды."""
    body = collection_lobby_body()
    (tmp_path / f"{COLLECTION_LOBBY}.yaml").write_text(
        yaml.safe_dump(body, allow_unicode=True), "utf-8"
    )
    published = await container_for(game.infra).bootstrap.execute(tmp_path)
    assert published == [COLLECTION_LOBBY]
    return body


async def test_round_plan_from_collection(
    game: Any, match_headers: dict[str, str], collection_lobby: dict[str, Any]
) -> None:
    items = [await game.add_item(domain="film", ready_for=FRAME_ASSETS) for _ in range(6)]
    outside = await films(game, 3, mode=FRAME_ASSETS)
    cid = await publish_collection(game, items)

    response = await plan(
        game, match_headers, mode_key=COLLECTION_LOBBY, rounds=5, collection_ids=[str(cid)]
    )

    assert response.status_code == 200, response.text
    assert response.json()["collections"] == [{"id": str(cid), "version": 3}]
    picked = {UUID(t) for t in targets(response)}
    assert picked <= set(items) and picked.isdisjoint(outside)
    assert_problem(
        await plan(
            game, match_headers, mode_key=COLLECTION_LOBBY, rounds=7, collection_ids=[str(cid)]
        ),
        422,
        "game.pool_too_small",
    )


async def test_round_plan_collection_errors(
    game: Any, match_headers: dict[str, str], collection_lobby: dict[str, Any]
) -> None:
    items = await films(game, 6, mode=FRAME_ASSETS)
    hidden = await publish_collection(game, items, status="hidden")
    music = await publish_collection(game, items, domain="music")
    visible = await publish_collection(game, items)

    async def call(ids: list[UUID]) -> Any:
        return await plan(
            game, match_headers, mode_key=COLLECTION_LOBBY, collection_ids=[str(i) for i in ids]
        )

    assert_problem(await call([]), 422, "game.collection_required")
    assert_problem(await call([hidden]), 404, "game.collection_not_found")
    assert_problem(await call([uuid7()]), 404, "game.collection_not_found")
    assert_problem(await call([visible, hidden]), 404, "game.collection_not_found")
    assert_problem(await call([music]), 422, "game.collection_domain_mismatch")
    too_many = await call([visible, uuid7(), uuid7(), uuid7()])
    assert_problem(too_many, 422, "game.too_many_collections")
    assert too_many.json()["details"] == {"given": 4, "max": 3}
    # Повтор одного сборника — один сборник.
    assert (await call([visible, visible])).status_code == 200
