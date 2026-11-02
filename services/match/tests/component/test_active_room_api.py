"""Одна активная комната: источник правды сервера (пункты 18, 22, 28 замечаний тестирования)."""

from __future__ import annotations

from typing import Any

import pytest
from match_app import MatchApp
from match_service.infrastructure.db.tables import admin_actions
from sqlalchemy import select

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

DUEL = "music.duel.classic"
PARTY = "film.party.frame"


async def _create(svc: MatchApp, host: Any, mode: str) -> Any:
    return await svc.client.post(
        "/v1/rooms",
        json={"mode_key": mode},
        headers={**host.headers, "Idempotency-Key": host.op()},
    )


async def _active(svc: MatchApp, player: Any) -> dict[str, Any]:
    response = await svc.client.get("/v1/rooms/active", headers=player.headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_active_room_endpoint_follows_create_and_leave(svc: MatchApp) -> None:
    a = svc.user()
    assert await _active(svc, a) == {"code": None, "phase": None}
    code = (await svc.create(a, DUEL))["code"]
    assert await _active(svc, a) == {"code": code, "phase": "lobby"}
    await svc.ok(a, code, "leave")
    assert await _active(svc, a) == {"code": None, "phase": None}


async def test_active_room_is_guest_forbidden(svc: MatchApp) -> None:
    response = await svc.client.get("/v1/rooms/active", headers=svc.guest().headers)
    assert response.status_code == 403


async def test_closed_tab_without_leave_does_not_block_new_room(app_factory: Any) -> None:
    """Вкладку закрыли без «Выйти»: после presence_timeout_s новая комната создаётся."""
    svc: MatchApp = await app_factory(presence_timeout_s=25)
    a = svc.user()
    code = (await svc.create(a, DUEL))["code"]
    svc.online.discard(a.user_id)  # heartbeat прекратился
    await svc.advance(code, [a], 20)
    conflict = assert_problem(await _create(svc, a, PARTY), 409, "match.active_room_conflict")
    assert conflict["details"] == {"code": code, "phase": "lobby"}
    await svc.advance(code, [a], 10)  # молчит 30 с > 25 с
    assert (await _create(svc, a, PARTY)).status_code == 201
    assert (await _active(svc, a))["code"] != code


async def test_explicit_join_by_silent_player_moves_him_to_new_room(app_factory: Any) -> None:
    svc: MatchApp = await app_factory(presence_timeout_s=25)
    a, b = svc.user(), svc.user()
    old = (await svc.create(a, DUEL))["code"]
    new = (await svc.create(b, PARTY))["code"]
    svc.online.discard(a.user_id)
    await svc.advance(old, [a], 30)
    await svc.ok(a, new, "join")
    assert (await _active(svc, a))["code"] == new


async def test_closed_room_answers_404_with_reason(svc: MatchApp) -> None:
    host, guest = svc.user(), svc.user()
    code = (await svc.create(host, DUEL))["code"]
    await svc.ok(host, code, "leave")  # единственный участник ушёл - комната закрыта
    body = assert_problem(await svc.post(guest, code, "join"), 404, "match.room_not_found")
    assert body["details"] == {"reason": "empty"}
    got = await svc.client.get(f"/v1/rooms/{code}", headers=guest.headers)
    assert assert_problem(got, 404, "match.room_not_found")["details"] == {"reason": "empty"}


async def test_unknown_room_reason_is_not_found(svc: MatchApp) -> None:
    body = assert_problem(
        await svc.post(svc.user(), "AAAAAAAA", "join"), 404, "match.room_not_found"
    )
    assert body["details"] == {"reason": "not_found"}


async def test_leave_with_other_players_keeps_room_open(svc: MatchApp) -> None:
    """Выход одного участника не закрывает комнату, пока в ней есть другие (пункт 22)."""
    host, guest = svc.user(), svc.user()
    code = (await svc.create(host, PARTY))["code"]
    await svc.join(code, guest)
    await svc.ok(host, code, "leave")
    state = await svc.state(guest, code)
    assert state["room"]["phase"] == "lobby"
    assert state["room"]["host_id"] == str(guest.user_id)


async def test_admin_reset_frees_stuck_pointer_and_is_audited(svc: MatchApp) -> None:
    admin = svc.user(perms=["admin.access", "match.manage"])
    a = svc.user()
    code = (await svc.create(a, DUEL))["code"]
    url = f"/admin/v1/players/{a.user_id}/active-room/reset"
    headers = {**admin.headers, "Idempotency-Key": "reset-pointer-0001"}
    denied = await svc.client.post(
        url, json={"reason": "тест"}, headers={**a.headers, "Idempotency-Key": "reset-0000002"}
    )
    assert denied.status_code == 403
    done = await svc.client.post(url, json={"reason": "застрял в 409"}, headers=headers)
    assert done.status_code == 200 and done.json() == {"released_code": code}
    assert (await _active(svc, a))["code"] is None  # комната жива, указатель снят
    assert (await _create(svc, a, PARTY)).status_code == 201
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        rows = (await session.execute(select(admin_actions))).mappings().all()
    reset = [r for r in rows if r["action"] == "player.room_pointer_reset"]
    assert (
        len(reset) == 1 and reset[0]["room_code"] == code and reset[0]["actor_id"] == admin.user_id
    )
