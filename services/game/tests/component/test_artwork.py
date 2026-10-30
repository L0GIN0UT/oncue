"""Картинка ответа (обложка, постер) в ревиле раунда (ADR 0073)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gv_testkit import assert_problem

from .test_stage3a_modes import RANDOM, bearer, bootstrap, give_up, start, stored

pytestmark = pytest.mark.component


async def _finished_reveal(game: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    done = (await give_up(game, token, snap)).json()
    return snap, done["round"]["reveal"]


async def test_reveal_has_artwork_link_when_set_is_ready(game: Any) -> None:
    await bootstrap(game)
    for _ in range(25):
        item = await game.add_item(ready_for=RANDOM)
        await game.ready_artwork(item)
    _, reveal = await _finished_reveal(game)
    artwork = reveal["artwork"]
    assert artwork["url"].startswith("/media/a/")
    assert artwork["expires_at"] > game.clock.now().isoformat()
    assert set(artwork) == {"url", "expires_at", "mime"}
    assert reveal["final_clue"]["kind"] == "audio"


async def test_reveal_artwork_is_null_without_set(game: Any) -> None:
    await bootstrap(game)
    for _ in range(25):
        await game.add_item(ready_for=RANDOM)
    _, reveal = await _finished_reveal(game)
    assert "artwork" in reveal
    assert reveal["artwork"] is None
    assert reveal["final_clue"]["url"]  # ревил цел


async def test_artwork_not_issued_before_round_is_finished(game: Any) -> None:
    await bootstrap(game)
    for _ in range(25):
        item = await game.add_item(ready_for=RANDOM)
        await game.ready_artwork(item)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    assert snap["rounds"][0]["reveal"] is None
    session = await stored(game, snap["id"])
    artwork_set = session.rounds[0].artwork_asset.asset_set_id
    assert all(issued[0] != artwork_set for issued in game.assets.issued)


async def test_blocked_artwork_does_not_break_reveal(game: Any) -> None:
    await bootstrap(game)
    for _ in range(25):
        item = await game.add_item(ready_for=RANDOM)
        await game.ready_artwork(item)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    session = await stored(game, snap["id"])
    game.assets.gone.add(session.rounds[0].artwork_asset.asset_set_id)
    done = (await give_up(game, token, snap)).json()
    reveal = done["round"]["reveal"]
    assert reveal["artwork"] is None
    assert reveal["final_clue"]["url"]


async def test_artwork_link_is_cached_between_reads(game: Any) -> None:
    await bootstrap(game)
    for _ in range(25):
        item = await game.add_item(ready_for=RANDOM)
        await game.ready_artwork(item)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    done = (await give_up(game, token, snap)).json()
    first = done["round"]["reveal"]["artwork"]
    again = (await game.client.get(f"/v1/sessions/{snap['id']}", headers=bearer(token))).json()
    assert again["rounds"][0]["reveal"]["artwork"] == first


async def test_other_player_cannot_read_reveal(game: Any) -> None:
    await bootstrap(game)
    for _ in range(25):
        item = await game.add_item(ready_for=RANDOM)
        await game.ready_artwork(item)
    snap, _ = await _finished_reveal(game)
    stranger = game.issuer.issue()
    response = await game.client.get(f"/v1/sessions/{snap['id']}", headers=bearer(stranger))
    assert_problem(response, 404, "game.session_not_found")


# ----------------------------------------------------------------- план раундов для match
async def test_round_plan_carries_artwork_asset_set_only_when_ready(
    game: Any, service_token: Callable[..., str]
) -> None:
    from .test_stage4_online import DUEL, bearer, plan, targets

    match_headers = bearer(service_token("match", "game", ["rounds.plan"]))

    await bootstrap(game)
    items = [await game.add_item(domain="film", ready_for=DUEL) for _ in range(6)]
    with_art = {item: await game.ready_artwork(item, "film") for item in items[:3]}

    response = await plan(game, match_headers, rounds=6)

    assert response.status_code == 200, response.text
    rounds = response.json()["rounds"]
    by_item = {r["answer"]["media_item_id"]: r["artwork_asset_set_id"] for r in rounds}
    assert len(by_item) == 6
    for item in items:
        expected = str(with_art[item]) if item in with_art else None
        assert by_item[str(item)] == expected
    assert set(targets(response)) == {str(i) for i in items}
