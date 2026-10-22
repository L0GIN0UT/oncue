"""Аудит безопасности match (этап 4a) через HTTP API и процесс rooms.

Номера — пункты аудита (A3–A11); владелец комнат на двойниках — ``tests/unit/test_hardening.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from match_app import MatchApp, Player
from match_doubles import TITLES
from match_service.infrastructure.container import container_for
from match_service.infrastructure.db.tables import admin_actions, idempotency_keys
from sqlalchemy import func, select

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_testkit import assert_problem, load_example

pytestmark = pytest.mark.component

ANONYMOUS = str(UUID(int=0))


async def _deleted(svc: MatchApp, user_id: UUID) -> None:
    base = load_example("identity.UserDeleted")
    payload = {**base.payload, "user_id": str(user_id), "version": 9}
    await svc.deliver(
        base.model_copy(
            update={"aggregate_id": str(user_id), "payload": payload, "event_id": uuid7()}
        )
    )


async def _duel(svc: MatchApp) -> tuple[Player, Player, str]:
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    room = (await svc.state(a, code))["room"]
    await svc.ok(a, code, "start", expected_version=room["version"])
    svc.clock.advance(seconds=3)
    await svc.phase(a, code, "level_open")
    return a, b, code


async def _finish_duel(svc: MatchApp) -> tuple[Player, Player, str]:
    """Дуэль до итога (угадал A, B сдался); итог записан в Postgres."""
    a, b, code = await _duel(svc)
    state = await svc.state(a, code)
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
    match_id = str(state["room"]["match_id"])
    path = f"/v1/matches/{match_id}"
    async with asyncio.timeout(5):
        while (await svc.client.get(path, headers=a.headers)).status_code != 200:  # noqa: ASYNC110 — опрос HTTP
            await asyncio.sleep(0.02)
    return a, b, match_id


async def _finished(svc: MatchApp, match_id: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            for event in await svc.outbox():
                if event.event_type == "match.MatchFinished" and (
                    event.payload["match_id"] == match_id
                ):
                    return dict(event.payload)
            await asyncio.sleep(0.02)


async def _count(svc: MatchApp, stmt: Any) -> int:
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        return int((await session.execute(stmt)).scalar_one())


# ----------------------------------------------------------------------------- A3
async def test_a3_tick_and_state_are_rate_limited(app_factory: Any) -> None:
    svc = await app_factory(tick_per_player_per_min=3, state_per_player_per_min=2)
    a = svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    for _ in range(3):
        assert (await svc.tick(a, code)).status_code == 200
    assert_problem(await svc.tick(a, code), 429, "match.rate_limited")
    path = f"/v1/rooms/{code}/state"
    for _ in range(2):
        assert (await svc.client.get(path, headers=a.headers)).status_code == 200
    assert_problem(await svc.client.get(path, headers=a.headers), 429, "match.rate_limited")


# ----------------------------------------------------------------------------- A4
async def test_a4_code_lookup_is_limited_per_user_too(app_factory: Any) -> None:
    svc = await app_factory(join_by_code_per_user_per_min=2)
    host, prober = svc.user(), svc.user()
    code = (await svc.create(host, "film.lobby.collection", collection_ids=[str(uuid7())]))["code"]
    for guess in ("ABCDEFGH", "ABCDEFGJ"):
        assert_problem(
            await svc.client.get(f"/v1/rooms/{guess}", headers=prober.headers),
            404,
            "match.room_not_found",
        )
    # Лимит исчерпан и для GET, и для join — даже по верному коду.
    assert_problem(
        await svc.client.get(f"/v1/rooms/{code}", headers=prober.headers), 429, "match.rate_limited"
    )
    assert_problem(await svc.post(prober, code, "join"), 429, "match.rate_limited")


# ----------------------------------------------------------------------------- A5
async def test_a5_user_deleted_mid_match_and_idempotency_keys_forgotten(svc: MatchApp) -> None:
    a, b, code = await _duel(svc)
    match_id = (await svc.state(a, code))["room"]["match_id"]
    await _deleted(svc, b.user_id)
    payload = await _finished(svc, match_id)
    ids = {p["user_id"] for p in payload["players"]}
    assert str(b.user_id) not in ids and ANONYMOUS in ids
    assert str(b.user_id) not in str(payload)

    # Оценка соперника оставляет ключ идемпотентности — после удаления аккаунта его нет.
    c, d, finished_id = await _finish_duel(svc)
    rate = await svc.client.post(
        f"/v1/matches/{finished_id}/rate",
        json={"to_user_id": str(d.user_id), "kind": "praise"},
        headers={**c.headers, "Idempotency-Key": "rate-key-del-1"},
    )
    assert rate.status_code == 204, rate.text
    keys_of_c = select(func.count()).where(idempotency_keys.c.actor_id == c.user_id)
    assert await _count(svc, keys_of_c) == 1
    await _deleted(svc, c.user_id)
    assert await _count(svc, keys_of_c) == 0


# ----------------------------------------------------------------------------- A6
async def test_a6_admin_abort_is_audited_and_event_has_machine_reason(svc: MatchApp) -> None:
    a, _, code = await _duel(svc)
    match_id = (await svc.state(a, code))["room"]["match_id"]
    admin = svc.user(perms=["admin.access", "match.manage"])
    reason = "жалоба: оскорбления в чате"
    for _ in range(2):  # повтор с тем же ключом — одна строка аудита
        response = await svc.client.post(
            f"/admin/v1/rooms/{code}/abort",
            json={"reason": reason},
            headers={**admin.headers, "Idempotency-Key": "abort-audit-0001"},
        )
        if response.status_code != 202:
            assert_problem(response, 409, "match.room_closed")
    payload = await _finished(svc, match_id)
    assert payload["aborted"] is True and payload["abort_reason"] == "admin"
    assert reason not in str(payload)
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        rows = (await session.execute(select(admin_actions))).mappings().all()
    (row,) = rows
    assert row["actor_id"] == admin.user_id and row["action"] == "room.abort"
    assert row["room_code"] == code and str(row["match_id"]) == match_id
    assert row["reason"] == reason and row["idempotency_key"] == "abort-audit-0001"


# ----------------------------------------------------------------------------- A8
async def test_a8_emotes_have_own_limit_and_respect_chat_slow(app_factory: Any) -> None:
    svc = await app_factory(emote_per_player_per_min=2)
    a, b = svc.user(), svc.user(sanctions=int(Sanction.CHAT_SLOW))
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    for _ in range(2):
        await svc.ok(a, code, "match/emote", kind="emote", key="match.emotes.gg")
    third = await svc.post(a, code, "match/emote", kind="emote", key="match.emotes.gg")
    assert_problem(third, 429, "match.rate_limited")
    await svc.ok(b, code, "match/emote", kind="emote", key="match.emotes.gg")
    slow = await svc.post(b, code, "match/emote", kind="emote", key="match.emotes.gg")
    assert_problem(slow, 429, "match.rate_limited")


# ----------------------------------------------------------------------------- A9
async def test_a9_kick_revokes_centrifugo_subscription(svc: MatchApp) -> None:
    host, b = svc.user(), svc.user()
    code = (await svc.create(host, "film.lobby.collection", collection_ids=[str(uuid7())]))["code"]
    await svc.join(code, b)
    await svc.ok(host, code, "kick", user_id=str(b.user_id))
    assert svc.subscriptions.revoked == [(b.user_id, f"room:{code}")]


# ----------------------------------------------------------------------------- A10
async def test_a10_match_result_hides_room_code_from_outsiders(svc: MatchApp) -> None:
    a, _, match_id = await _finish_duel(svc)
    path = f"/v1/matches/{match_id}"
    mine = (await svc.client.get(path, headers=a.headers)).json()
    other = (await svc.client.get(path, headers=svc.user().headers)).json()
    assert mine["room_code"] is not None and other["room_code"] is None


# ----------------------------------------------------------------------------- A11
async def test_a11_parallel_creates_by_one_player_make_one_room(svc: MatchApp) -> None:
    a = svc.user()

    async def create(i: int) -> Any:
        return await svc.client.post(
            "/v1/rooms",
            json={"mode_key": "music.duel.classic"},
            headers={**a.headers, "Idempotency-Key": f"idem-race-{i:04d}"},
        )

    responses = await asyncio.gather(*(create(i) for i in range(6)))
    created = [r for r in responses if r.status_code == 201]
    assert len(created) == 1, [r.text for r in responses]
    for r in responses:
        if r.status_code != 201:
            assert_problem(r, 409, "match.active_room_conflict")
    container = container_for(svc.infra)
    assert await container.player_rooms.get(a.user_id) == created[0].json()["code"]
