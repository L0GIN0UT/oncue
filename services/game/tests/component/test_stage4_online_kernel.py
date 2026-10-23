"""Онлайн-режимы game на плагинах состязания ядра (поток A этапа 4a: ``ContestRule``,
``party_speed``, несколько сборников в ``DrawContext.collection_ids``).

До слияния потока A модуль пропускается: в реестре ядра нет ``contest``, сиды party и
лобби не проходят проверку (``scoring.type`` ``party_speed`` неизвестен), а источник
``collection`` читает только первый сборник. Оркестратор включает модуль слиянием A —
отдельной правки не нужно.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from gv_core.ids import uuid7
from gv_game_kernel import PluginRegistry
from gv_messaging import Outcome
from gv_testkit import assert_problem, load_example

pytestmark = [
    pytest.mark.component,
    pytest.mark.skipif(
        not hasattr(PluginRegistry, "contest"),
        reason="ожидает слияния потока A этапа 4a: реестр ContestRule, party_speed, "
        "collection_ids в источнике collection",
    ),
]

SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"
ONLINE = {
    "music.duel.classic",
    "film.duel.frame",
    "film.party.frame",
    "music.lobby.classic",
    "film.lobby.collection",
    # 4b: «лайт» и рейтинговые шаблоны (шаг 0b)
    "music.duel.lite",
    "film.duel.lite",
    "music.ranked.classic",
    "music.ranked.lite",
    "film.ranked.classic",
    "film.ranked.lite",
}
LOBBY = "film.lobby.collection"
FRAME_ASSETS = "film.collection.frame"


def seed(key: str) -> dict[str, Any]:
    body: dict[str, Any] = yaml.safe_load((SEEDS_DIR / f"{key}.yaml").read_text("utf-8"))
    return body


@pytest.fixture
def headers(service_token: Callable[..., str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {service_token('match', 'game', ['rounds.plan'])}"}


async def bootstrap(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"


async def publish_collection(game: Any, items: list[UUID]) -> UUID:
    cid = uuid7()
    base = load_example("collections.CollectionPublished")
    payload = {
        **base.payload,
        "collection_id": str(cid),
        "version": 1,
        "content_version": 1,
        "domain": "film",
        "status": "published",
        "default_mode_key": "film.collection.frame",
        "items": [{"media_item_id": str(i), "position": p} for p, i in enumerate(items)],
    }
    envelope = base.model_copy(update={"aggregate_id": str(cid), "payload": payload})
    assert await game.deliver(envelope) is Outcome.PROCESSED
    return cid


async def test_all_online_seeds_are_published_and_listed(game: Any) -> None:
    await bootstrap(game)

    online = (await game.client.get("/v1/modes", params={"session": "online"})).json()

    assert {m["key"] for m in online} == ONLINE
    lobby = next(m for m in online if m["key"] == LOBBY)
    assert lobby["online"]["kind"] == "lobby" and lobby["online"]["max_collections"] == 3
    assert lobby["online"]["chat"]["free_text"] is True
    assert lobby["online"]["lobby"]["overridable"]["rounds"] == [5, 10, 12, 20]
    assert lobby["round_options"] is None and lobby["min_items"] == 5


async def test_contest_params_are_validated_on_publish(game: Any) -> None:
    body = copy.deepcopy(seed("film.party.frame"))
    body["session"]["contest"]["params"] = {"attempts_per_level": 0}
    response = await game.client.post(
        "/admin/v1/mode-configs/validate",
        json={"body": body},
        headers=game.admin("config.modes.view"),
    )
    assert response.json()["ok"] is False
    assert any(e["path"].startswith("session.contest") for e in response.json()["errors"])


async def test_party_round_plan(game: Any, headers: dict[str, str]) -> None:
    await bootstrap(game)
    for _ in range(12):
        await game.add_item(domain="film", ready_for="film.party.frame")

    response = await game.client.post(
        "/internal/v1/round-plans",
        json={"mode_key": "film.party.frame", "seed": "match-party-0001", "rounds": 10},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["rounds"]) == 10


async def test_lobby_plan_from_several_collections(game: Any, headers: dict[str, str]) -> None:
    await bootstrap(game)
    first = [await game.add_item(domain="film", ready_for=FRAME_ASSETS) for _ in range(6)]
    second = [await game.add_item(domain="film", ready_for=FRAME_ASSETS) for _ in range(6)]
    a, b = await publish_collection(game, first), await publish_collection(game, second)

    response = await game.client.post(
        "/internal/v1/round-plans",
        json={
            "mode_key": LOBBY,
            "seed": "match-lobby-0001",
            "rounds": 12,
            "collection_ids": [str(a), str(b)],
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    picked = {UUID(r["answer"]["media_item_id"]) for r in response.json()["rounds"]}
    assert picked == set(first) | set(second)
    assert [c["id"] for c in response.json()["collections"]] == [str(a), str(b)]
    assert_problem(
        await game.client.post(
            "/internal/v1/round-plans",
            json={
                "mode_key": LOBBY,
                "seed": "match-lobby-0001",
                "rounds": 13,
                "collection_ids": [str(a), str(b)],
            },
            headers=headers,
        ),
        422,
        "game.pool_too_small",
    )
