"""Consumers и история (R7, R8): идемпотентность по inbox и ``version``, обезличивание,
итог матча и оценки одной транзакцией с outbox (повтор — no-op)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from match_app import MatchApp, Player
from match_doubles import TITLES
from match_service.infrastructure.container import container_for
from match_service.infrastructure.rooms import keys
from match_service.infrastructure.rooms.codec import loads

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, assert_problem, load_example

pytestmark = pytest.mark.component


async def _profile(svc: MatchApp, user_id: UUID, nickname: str, version: int) -> Outcome:
    base = load_example("identity.UserProfileBasicsChanged")
    payload = {**base.payload, "user_id": str(user_id), "nickname": nickname, "version": version}
    return await svc.deliver(
        base.model_copy(
            update={"aggregate_id": str(user_id), "payload": payload, "event_id": uuid7()}
        )
    )


async def _deleted(svc: MatchApp, user_id: UUID, version: int = 9) -> Outcome:
    base = load_example("identity.UserDeleted")
    payload = {**base.payload, "user_id": str(user_id), "version": version}
    return await svc.deliver(
        base.model_copy(
            update={"aggregate_id": str(user_id), "payload": payload, "event_id": uuid7()}
        )
    )


async def _play_duel(svc: MatchApp) -> tuple[Player, Player, str, str]:
    """Дуэль до итога: B сдаётся после первого раунда."""
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    room = (await svc.state(a, code))["room"]
    await svc.ok(a, code, "start", expected_version=room["version"])
    svc.clock.advance(seconds=3)
    state = await svc.phase(a, code, "level_open")
    match = state["match"]
    await svc.ok(
        a,
        code,
        "match/guess",
        expected_version=state["room"]["version"],
        round_index=match["round_index"],
        level=match["level"],
        answer={"text": TITLES[0]},
    )
    await svc.ok(b, code, "match/forfeit")
    summary = await svc.phase(a, code, "summary")
    match_id = summary["room"]["match_id"]
    async with asyncio.timeout(5):
        while True:
            response = await svc.client.get(f"/v1/matches/{match_id}", headers=a.headers)
            if response.status_code == 200:
                break
            await asyncio.sleep(0.02)
    return a, b, code, match_id


async def test_profile_projection_respects_version_and_renames_in_room(svc: MatchApp) -> None:
    a = svc.user()
    assert await _profile(svc, a.user_id, "first", 2) is Outcome.PROCESSED
    code = (await svc.create(a, "music.duel.classic"))["code"]
    assert (await svc.state(a, code))["room"]["players"][0]["nickname"] == "first"
    assert await _profile(svc, a.user_id, "stale", 1) is Outcome.PROCESSED
    assert await _profile(svc, a.user_id, "renamed", 3) is Outcome.PROCESSED
    state = await svc.until(a, code, lambda s: s["room"]["players"][0]["nickname"] == "renamed")
    assert state["room"]["players"][0]["nickname"] == "renamed"
    lobby = [m for m in svc.realtime.room(code) if m["kind"] == "lobby"]
    assert lobby and lobby[-1]["delta"]["room"]["players"][0]["nickname"] == "renamed"


async def test_user_deleted_leaves_room_and_history_is_anonymized(svc: MatchApp) -> None:
    a, b, _, match_id = await _play_duel(svc)
    c, d = svc.user(), svc.user()
    code = (await svc.create(c, "film.lobby.collection", collection_ids=[str(uuid7())]))["code"]
    await svc.join(code, d)

    assert await _deleted(svc, b.user_id) is Outcome.PROCESSED
    assert await _deleted(svc, d.user_id) is Outcome.PROCESSED
    state = await svc.until(c, code, lambda s: len(s["room"]["players"]) == 1)
    assert state["room"]["players"][0]["user_id"] == str(c.user_id)
    result = (await svc.client.get(f"/v1/matches/{match_id}", headers=a.headers)).json()
    ids = {p["user_id"] for p in result["players"]}
    assert str(b.user_id) not in ids and str(UUID(int=0)) in ids
    rounds_ids = {r["user_id"] for rnd in result["rounds"] for r in rnd["results"]}
    assert str(b.user_id) not in rounds_ids
    assert (await svc.client.get("/v1/me/matches", headers=b.headers)).json()["items"] == []


async def test_match_finished_written_once_and_repeat_is_noop(svc: MatchApp) -> None:
    a, b, code, match_id = await _play_duel(svc)
    events = [e for e in await svc.outbox() if e.event_type == "match.MatchFinished"]
    (finished,) = events
    assert_event_matches_schema(finished)
    assert finished.payload["match_id"] == match_id and finished.payload["aborted"] is False
    # Повтор записи итога (новый владелец после handoff) — без второй строки и события.
    raw = await svc.redis.get(keys.snapshot(code))
    assert raw is not None
    room = loads(raw)
    assert room.match is not None
    records = container_for(svc.infra).records
    assert await records.record_finished(room, room.match) is False
    assert len([e for e in await svc.outbox() if e.event_type == "match.MatchFinished"]) == 1
    del a, b


async def test_rate_player_once_per_pair(svc: MatchApp) -> None:
    a, b, _, match_id = await _play_duel(svc)
    path = f"/v1/matches/{match_id}/rate"

    def headers(player: Player, key: str) -> dict[str, Any]:
        return {**player.headers, "Idempotency-Key": key}

    body = {"to_user_id": str(b.user_id), "kind": "praise"}
    first = await svc.client.post(path, json=body, headers=headers(a, "rate-key-0001"))
    assert first.status_code == 204, first.text
    again = await svc.client.post(path, json=body, headers=headers(a, "rate-key-0001"))
    assert again.status_code == 204
    other_key = await svc.client.post(path, json=body, headers=headers(a, "rate-key-0002"))
    assert_problem(other_key, 409, "match.already_rated")
    myself = await svc.client.post(
        path, json={"to_user_id": str(a.user_id), "kind": "report"}, headers=headers(a, "rk-0003x")
    )
    assert_problem(myself, 422, "match.cannot_rate_self")
    outsider = await svc.client.post(path, json=body, headers=headers(svc.user(), "rk-0004x"))
    assert_problem(outsider, 403, "match.not_participant")
    missing = await svc.client.post(
        f"/v1/matches/{uuid7()}/rate", json=body, headers=headers(a, "rk-0005x")
    )
    assert_problem(missing, 404, "match.match_not_found")
    (rated,) = [e for e in await svc.outbox() if e.event_type == "match.PlayerRated"]
    assert_event_matches_schema(rated)


async def test_mode_heads_projection(svc: MatchApp) -> None:
    from match_service.infrastructure.db.repositories import SqlModeHeads

    heads = SqlModeHeads(svc.infra.sessions)
    base = load_example("game.ModeConfigPublished")
    for version in (2, 1):
        payload = {**base.payload, "mode_key": "music.duel.classic", "version": version}
        envelope = base.model_copy(update={"payload": payload, "event_id": uuid7()})
        assert await svc.deliver(envelope) is Outcome.PROCESSED
    assert await heads.current("music.duel.classic") == 2
    archived = load_example("game.ModeConfigArchived")
    payload = {**archived.payload, "mode_key": "music.duel.classic", "version": 2}
    envelope = archived.model_copy(update={"payload": payload, "event_id": uuid7()})
    assert await svc.deliver(envelope) is Outcome.PROCESSED
    assert await heads.current("music.duel.classic") is None
