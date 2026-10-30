"""Сценарии демонстрации 4a (план этапа 4, §0 п. 2–8б) через HTTP API и процесс rooms.

Каждое сообщение канала проверяется по ``contracts/realtime`` (``FakeRealtime``), версии
канала идут подряд; античит — рекурсивный обход JSON ответов, снимков, tick и сообщений.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from match_app import MatchApp, Player
from match_doubles import TITLES, plan_factory, seed_bodies
from match_service.settings import Settings

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

SECRET_KEYS = frozenset({"media_item_id", "aliases", "title_aliases", "asset_set_id", "answer"})


def _walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def assert_no_secrets(payload: Any, titles: list[str], *, texts: tuple[str, ...] = ()) -> None:
    """Античит (§12 п. 1): до ревила нет единицы раунда, ключей ответа, плана; чужих текстов."""
    for key, value in _walk(payload):
        assert key not in SECRET_KEYS, (key, payload)
        if isinstance(value, str):
            for title in titles:
                assert title.lower() not in value.lower(), (title, payload)
            for text in texts:
                assert text not in value, (text, payload)


def _channel(svc: MatchApp, code: str) -> list[Any]:
    return [dict(m) for m in svc.realtime.room(code)]


async def _start(svc: MatchApp, host: Player, code: str) -> None:
    room = (await svc.state(host, code))["room"]
    await svc.ok(host, code, "start", expected_version=room["version"])
    svc.clock.advance(seconds=3)
    await svc.phase(host, code, "level_open")


def _guess(state: dict[str, Any], text: str) -> dict[str, Any]:
    match = state["match"]
    return {
        "expected_version": state["room"]["version"],
        "round_index": match["round_index"],
        "level": match["level"],
        "answer": {"text": text},
    }


# ----------------------------------------------------------------------------- п. 2
async def test_custom_lobby_by_collection_settings_reset_ready_and_start(
    svc: MatchApp,
) -> None:
    a, b, c, d = (svc.user() for _ in range(4))
    await svc.nickname(a, "anna")
    collection = uuid7()
    room = await svc.create(
        a,
        "film.lobby.collection",
        settings={"rounds": 12, "level_window_s": 25, "visibility": "private"},
        collection_ids=[str(collection)],
    )
    code = room["code"]
    assert room["players"][0]["nickname"] == "anna" and room["options"]["rounds"]
    for player in (b, c, d):
        found = await svc.client.get(f"/v1/rooms/{code}", headers=player.headers)
        assert found.status_code == 200 and found.json()["code"] == code
    await svc.join(code, b, c, d)

    changed = await svc.ok(a, code, "settings", settings={"level_window_s": 15})
    assert not any(p["ready"] for p in changed["room"]["players"])
    not_ready = await svc.post(a, code, "start", expected_version=changed["version"])
    assert_problem(not_ready, 409, "match.not_all_ready")
    for player in (b, c, d):
        await svc.ok(player, code, "ready", ready=True)
    await svc.ok(a, code, "settings", settings={"level_window_s": 25})
    for player in (b, c, d):
        await svc.ok(player, code, "ready", ready=True)

    await _start(svc, a, code)
    (request,) = svc.plans.requests
    assert request.rounds == 12 and list(request.collection_ids) == [collection]
    state = await svc.state(b, code)
    assert state["match"]["rounds_total"] == 12 and state["match"]["clue"] is not None
    svc.realtime.assert_versions_contiguous(f"room:{code}")
    assert_no_secrets(_channel(svc, code), TITLES[:12])


# ----------------------------------------------------------------------------- п. 3, 8
async def test_party_of_four_statuses_without_texts_and_summary(svc: MatchApp) -> None:
    a, b, c, d = (svc.user() for _ in range(4))
    code = (await svc.create(a, "film.party.frame"))["code"]
    await svc.join(code, b, c, d)
    await _start(svc, a, code)
    players = (a, b, c, d)

    secret = f"секрет-{uuid7().hex[-6:]}"
    miss = await svc.ok(b, code, "match/guess", **_guess(await svc.state(b, code), secret))
    assert miss["result"] == "miss" and miss["me"]["guesses"][0]["display"] == secret
    assert_no_secrets(miss, TITLES[:1])  # ответ команды: только своё, без ключа ответа
    ticked = (await svc.tick(d, code, known=0)).json()
    assert_no_secrets(ticked, TITLES[:1], texts=(secret,))
    seen_by_c = await svc.state(c, code)
    statuses = {p["user_id"]: p["status"] for p in seen_by_c["match"]["players"]}
    assert statuses[str(b.user_id)] == "answered"
    assert_no_secrets(seen_by_c, TITLES[:1], texts=(secret,))

    for round_index in range(10):
        state = await svc.until(
            a,
            code,
            lambda s, i=round_index: (
                s["match"] is not None
                and s["match"]["round_index"] == i
                and s["room"]["phase"] == "level_open"
            ),
        )
        assert_no_secrets(state, [TITLES[round_index]])
        # Пасовавший на уровне 1 (B в раунде 0) отвечает на уровне 2.
        while (await svc.state(a, code))["room"]["phase"] == "level_open":
            for player in players:
                fresh = await svc.state(player, code)
                if fresh["room"]["phase"] != "level_open" or fresh["me"]["attempts_left"] == 0:
                    continue
                await svc.ok(player, code, "match/guess", **_guess(fresh, TITLES[round_index]))
        reveal = await svc.phase(a, code, "reveal")
        assert reveal["match"]["reveal"]["title"] == TITLES[round_index]
        svc.clock.advance(seconds=5)
    summary = await svc.phase(a, code, "summary")
    match_id = summary["room"]["match_id"]

    messages = _channel(svc, code)
    svc.realtime.assert_versions_contiguous(f"room:{code}")
    assert {m["kind"] for m in messages} >= {"phase", "level_opened", "round_reveal", "summary"}
    for message in messages:
        if message["kind"] not in ("round_reveal", "summary", "level_closed"):
            assert_no_secrets(message, TITLES[:10], texts=(secret,))
        else:
            assert secret not in json.dumps(message, ensure_ascii=False)

    async with asyncio.timeout(5):  # итог пишется после снимка summary
        while not any(e.event_type == "match.MatchFinished" for e in await svc.outbox()):  # noqa: ASYNC110 — опрос Postgres
            await asyncio.sleep(0.02)
    events = {e.event_type: e for e in await svc.outbox()}
    for event_type in ("match.MatchStarted", "match.MatchFinished"):
        assert_event_matches_schema(events[event_type])
    result = (await svc.client.get(f"/v1/matches/{match_id}", headers=a.headers)).json()
    assert len(result["rounds"]) == 10
    # Время по FakeClock не шло: party_speed даёт всем максимум — ничья (места 1, 1, 1, 1).
    assert {p["place"] for p in result["players"]} == {1}
    mine = (await svc.client.get("/v1/me/matches", headers=b.headers)).json()
    assert [m["match_id"] for m in mine["items"]] == [match_id]
    assert mine["items"][0]["outcome"] is None and mine["items"][0]["players"] == 4


# ----------------------------------------------------------------------------- п. 4
async def test_party_disconnect_reconnect_and_drop_after_grace(app_factory: Any) -> None:
    svc = await app_factory(presence_timeout_s=12)
    a, b, c = (svc.user() for _ in range(3))
    code = (await svc.create(a, "film.party.frame"))["code"]
    await svc.join(code, b, c)
    await _start(svc, a, code)
    svc.online.discard(c.user_id)  # C закрыл вкладку
    for _ in range(5):
        await svc.advance(code, (a, b, c), 3)
    seen = await svc.until(
        a,
        code,
        lambda s: (
            {p["user_id"]: p["presence"] for p in s["room"]["players"]}[str(c.user_id)]
            == "disconnected"
        ),
    )
    assert seen["room"]["phase"] in ("level_open", "reveal")
    presence = [
        m["delta"]["presence"]
        for m in _channel(svc, code)
        if m["kind"] == "player" and m["delta"]["user_id"] == str(c.user_id)
    ]
    assert "disconnected" in presence

    back = await svc.post(c, code, "join")  # вернулся по ссылке
    assert back.status_code == 200
    svc.online.add(c.user_id)
    await svc.advance(code, (a, b, c), 1)
    state = await svc.until(
        c,
        code,
        lambda s: (
            {p["user_id"]: p["presence"] for p in s["room"]["players"]}[str(c.user_id)] == "online"
        ),
    )
    assert state["room"]["phase_ends_at"] is not None and state["match"] is not None

    svc.online.discard(c.user_id)
    for _ in range(16):  # 48 с > presence_timeout + disconnect_grace_s
        await svc.advance(code, (a, b, c), 3)
    left = await svc.until(
        a,
        code,
        lambda s: (
            {p["user_id"]: p["presence"] for p in s["room"]["players"]}[str(c.user_id)] == "left"
        ),
    )
    assert left["room"]["phase"] in ("level_open", "reveal")
    gone = await svc.client.get(f"/v1/rooms/{code}/state", headers=c.headers)
    assert_problem(gone, 403, "match.not_participant")


# ----------------------------------------------------------------------------- п. 5
async def test_duel_race_forfeit_and_rematch(svc: MatchApp) -> None:
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    await _start(svc, a, code)
    first = await svc.state(a, code)
    win = await svc.ok(a, code, "match/guess", **_guess(first, TITLES[0]))
    assert win["result"] == "exact" and win["level_closed"] is True
    late = await svc.post(b, code, "match/guess", **_guess(first, "наугад"))
    assert_problem(late, 409, "match.phase_mismatch")
    second = await svc.until(b, code, lambda s: s["match"]["level"] == 2)
    assert second["me"]["attempts_left"] == 3

    await svc.ok(b, code, "match/forfeit")
    summary = await svc.phase(a, code, "summary")
    placements = next(m for m in _channel(svc, code) if m["kind"] == "summary")["delta"]
    by_user = {p["user_id"]: p for p in placements["placements"]}
    assert by_user[str(a.user_id)]["outcome"] == "win"
    assert by_user[str(b.user_id)]["finish"] == "forfeit"
    assert summary["room"]["phase"] == "summary"

    # Сдавшийся остаётся в комнате: реванш ждёт его согласия («Ждём остальных…»).
    waiting = await svc.ok(a, code, "rematch")
    assert waiting["rematch_code"] is None
    assert (await svc.state(b, code))["room"]["phase"] == "summary"
    rematch = await svc.ok(b, code, "rematch")
    new_code = rematch["rematch_code"]
    assert new_code and new_code != code
    fresh = await svc.client.get(f"/v1/rooms/{new_code}", headers=a.headers)
    assert fresh.status_code == 200 and fresh.json()["host_id"] == str(a.user_id)
    players = {p["user_id"] for p in (await svc.state(b, new_code))["room"]["players"]}
    assert players == {str(a.user_id), str(b.user_id)}
    # Старая комната закрыта: код реванша — в дельте closed и в state для вернувшихся.
    closed = [m["delta"] for m in _channel(svc, code) if m["kind"] == "closed"]
    assert closed == [{"reason": "rematch", "rematch_code": new_code}]
    old = await svc.state(a, code)
    assert old["room"]["phase"] == "closed" and old["room"]["rematch_code"] == new_code
    svc.realtime.assert_versions_contiguous(f"room:{code}")


# ----------------------------------------------------------------------------- п. 7
async def test_match_continues_on_http_tick_when_centrifugo_is_down(svc: MatchApp) -> None:
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    svc.realtime.unavailable()
    await _start(svc, a, code)
    tick = (await svc.tick(b, code, known=0)).json()
    assert tick["state"]["room"]["phase"] == "level_open"
    known = tick["version"]
    await svc.ok(a, code, "match/guess", **_guess(tick["state"], TITLES[0]))
    await svc.until(b, code, lambda s: s["room"]["version"] > known)
    after = (await svc.tick(b, code, known=known)).json()
    assert after["state"] is not None and after["state"]["match"]["level"] == 2
    unchanged = (await svc.tick(b, code, known=after["version"])).json()
    assert unchanged["state"] is None
    token = await svc.client.post(f"/v1/rooms/{code}/realtime-token", headers=b.headers)
    assert token.status_code == 200 and token.json()["channel"] == f"room:{code}"
    outsider = await svc.client.post(f"/v1/rooms/{code}/realtime-token", headers=svc.user().headers)
    assert_problem(outsider, 403, "match.not_participant")


# ----------------------------------------------------------------------------- п. 8б
async def test_lobby_free_chat_denylist_and_duel_emotes_only(svc: MatchApp) -> None:
    a, b = svc.user(), svc.user()
    lobby = (await svc.create(a, "film.lobby.collection", collection_ids=[str(uuid7())]))["code"]
    await svc.join(lobby, b)
    await svc.ok(a, lobby, "match/chat", text="удачи всем")
    bad = await svc.post(b, lobby, "match/chat", text="ну ты и хуй")
    problem = assert_problem(bad, 422, "match.chat_rejected")
    assert problem["details"] == {"reason": "denylist"}
    link = await svc.post(b, lobby, "match/chat", text="заходи на evil.example.com")
    assert assert_problem(link, 422, "match.chat_rejected")["details"] == {"reason": "link"}
    chats = [m["delta"]["text"] for m in _channel(svc, lobby) if m["kind"] == "chat"]
    assert chats == ["удачи всем"]

    muted = svc.user(sanctions=int(Sanction.CHAT_MUTE))
    await svc.ok(a, lobby, "leave")
    await svc.ok(b, lobby, "leave")
    c, d = svc.user(), svc.user()
    duel = (await svc.create(c, "music.duel.classic"))["code"]
    await svc.join(duel, d)
    disabled = await svc.post(c, duel, "match/chat", text="привет")
    assert_problem(disabled, 403, "match.chat_disabled")
    await svc.ok(d, duel, "match/emote", kind="phrase", key="match.phrases.good_luck")
    emotes = [m["delta"] for m in _channel(svc, duel) if m["kind"] == "emote"]
    assert emotes[0]["phrase"] == "match.phrases.good_luck" and emotes[0]["emote"] is None
    silenced = await svc.post(muted, duel, "match/emote", kind="emote", key="match.emotes.gg")
    assert_problem(silenced, 403, "match.chat_muted")


async def test_chat_slow_sanction_limits_rate(svc: MatchApp) -> None:
    host = svc.user()
    slow = svc.user(sanctions=int(Sanction.CHAT_SLOW))
    code = (await svc.create(host, "film.lobby.collection", collection_ids=[str(uuid7())]))["code"]
    await svc.join(code, slow)
    await svc.ok(slow, code, "match/chat", text="первое")
    second = await svc.post(slow, code, "match/chat", text="второе")
    problem = assert_problem(second, 429, "match.rate_limited")
    assert second.headers["retry-after"] and problem["details"]["retry_after"] >= 1
    await svc.ok(host, code, "match/chat", text="хост без санкций")


# ----------------------------------------------------------------------------- прочее
async def test_create_room_rules(svc: MatchApp) -> None:
    a = svc.user()
    key = {"Idempotency-Key": "idem-room-0001"}
    body = {"mode_key": "music.duel.classic"}
    first = await svc.client.post("/v1/rooms", json=body, headers={**a.headers, **key})
    again = await svc.client.post("/v1/rooms", json=body, headers={**a.headers, **key})
    assert first.status_code == 201 and again.json()["code"] == first.json()["code"]
    reused = await svc.client.post(
        "/v1/rooms", json={"mode_key": "film.duel.frame"}, headers={**a.headers, **key}
    )
    assert_problem(reused, 409, "idempotency.key_reused")
    busy = await svc.client.post(
        "/v1/rooms", json=body, headers={**a.headers, "Idempotency-Key": "idem-room-0002"}
    )
    assert_problem(busy, 409, "match.active_room_conflict")

    b = svc.user()
    unknown = await svc.client.post(
        "/v1/rooms",
        json={"mode_key": "film.nope.mode"},
        headers={**b.headers, "Idempotency-Key": "idem-room-0003"},
    )
    assert_problem(unknown, 404, "match.mode_not_found")
    not_allowed = await svc.client.post(
        "/v1/rooms",
        json={"mode_key": "music.duel.classic", "settings": {"rounds": 3}},
        headers={**b.headers, "Idempotency-Key": "idem-room-0004"},
    )
    assert_problem(not_allowed, 422, "match.setting_not_allowed")
    need_collection = await svc.client.post(
        "/v1/rooms",
        json={"mode_key": "film.lobby.collection"},
        headers={**b.headers, "Idempotency-Key": "idem-room-0005"},
    )
    assert_problem(need_collection, 422, "match.collection_required")


async def test_room_name_is_stored_validated_and_shown_in_lobby_list(svc: MatchApp) -> None:
    a, b, c = svc.user(), svc.user(), svc.user()
    body = {"settings": {"visibility": "public"}, "collection_ids": [str(uuid7())]}
    named = await svc.create(a, "film.lobby.collection", name="  Вечер кино  ", **body)
    assert named["settings"]["name"] == "Вечер кино"  # нормализация как у чата
    unnamed = await svc.create(b, "film.lobby.collection", **body)
    assert unnamed["settings"]["name"] is None
    state = await svc.state(a, named["code"])
    assert state["room"]["settings"]["name"] == "Вечер кино"
    page = (await svc.client.get("/v1/rooms/open", headers=c.headers)).json()
    names = {r["code"]: r["settings"]["name"] for r in page["items"]}
    assert names == {named["code"]: "Вечер кино", unnamed["code"]: None}
    hosts = {r["code"]: r["host_id"] for r in page["items"]}
    assert hosts == {named["code"]: str(a.user_id), unnamed["code"]: str(b.user_id)}

    limit = Settings().match.room_name_max_length
    for n, (name, reason) in enumerate(
        [
            ("х" * (limit + 1), "too_long"),
            ("заходи на evil.example.com", "link"),
            ("хуй", "denylist"),
        ]
    ):
        denied = await svc.client.post(
            "/v1/rooms",
            json={"mode_key": "music.duel.classic", "name": name},
            headers={**c.headers, "Idempotency-Key": f"idem-name-000{n}"},
        )
        problem = assert_problem(denied, 422, "match.room_name_rejected")
        assert problem["details"] == {"reason": reason}


async def test_open_lobbies_list_and_room_not_found(svc: MatchApp) -> None:
    a, b = svc.user(), svc.user()
    public = await svc.create(
        a,
        "film.lobby.collection",
        settings={"visibility": "public"},
        collection_ids=[str(uuid7())],
    )
    await svc.create(b, "music.duel.classic")
    page = (await svc.client.get("/v1/rooms/open", headers=a.headers)).json()
    assert [r["code"] for r in page["items"]] == [public["code"]]
    missing = await svc.client.get("/v1/rooms/ABCDEFGH", headers=a.headers)
    assert_problem(missing, 404, "match.room_not_found")


async def test_start_failure_after_202_is_visible_in_lobby(app_factory: Any) -> None:
    svc = await app_factory(plan_delay_s=0.3, reply_timeout_ms=50)
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, "film.lobby.collection", collection_ids=[str(uuid7())]))["code"]
    # Таймаут ответа 50 мс: вход и готовность тоже могут вернуть 202 — ждём по state.
    for path, body in (("join", {}), ("ready", {"ready": True})):
        sent = await svc.post(b, code, path, **body)
        assert sent.status_code in (200, 202), sent.text
    svc.online.add(b.user_id)
    await svc.until(
        a,
        code,
        lambda s: len(s["room"]["players"]) == 2 and s["room"]["players"][1]["ready"],
    )
    svc.plans.unavailable()
    version = (await svc.state(a, code))["room"]["version"]
    accepted = await svc.post(a, code, "start", expected_version=version)
    assert accepted.status_code == 202, accepted.text
    state = await svc.until(a, code, lambda s: s["room"]["start_error"] is not None)
    assert state["room"]["phase"] == "lobby"
    assert state["room"]["start_error"]["code"] == "match.game_unavailable"
    lobby = [m for m in _channel(svc, code) if m["kind"] == "lobby"]
    assert lobby[-1]["delta"]["room"]["start_error"]["code"] == "match.game_unavailable"
    svc.realtime.assert_versions_contiguous(f"room:{code}")
    # Смена настроек сбрасывает причину.
    changed = await svc.ok(a, code, "settings", settings={"rounds": 10})
    assert changed["room"]["start_error"] is None


async def test_clue_and_format_overrides_outside_template_options_are_refused(
    svc: MatchApp,
) -> None:
    a = svc.user()
    room = await svc.create(a, "film.lobby.collection", collection_ids=[str(uuid7())])
    assert room["options"]["clue_type"] is None and room["options"]["answer_format"] is None
    for field, value in (("clue_type", "poster_masked"), ("answer_format", "pick")):
        response = await svc.post(a, room["code"], "settings", settings={field: value})
        problem = assert_problem(response, 422, "match.setting_not_allowed")
        assert problem["details"] == {"field": field}
    created = await svc.client.post(
        "/v1/rooms",
        json={"mode_key": "music.duel.classic", "settings": {"answer_format": "pick"}},
        headers={**svc.user().headers, "Idempotency-Key": "idem-override-1"},
    )
    assert_problem(created, 422, "match.setting_not_allowed")


async def test_plan_failure_keeps_lobby_and_admin_abort(svc: MatchApp) -> None:
    a, b = svc.user(), svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    svc.plans.pool_too_small(available=1)
    version = (await svc.state(a, code))["room"]["version"]
    small = await svc.post(a, code, "start", expected_version=version)
    assert_problem(small, 422, "match.pool_too_small")
    failed = (await svc.state(a, code))["room"]["start_error"]
    assert failed["code"] == "match.pool_too_small"
    svc.plans.unavailable()
    down = await svc.post(a, code, "start", expected_version=version)
    assert_problem(down, 503, "match.game_unavailable")
    svc.plans.heal()
    await _start(svc, a, code)

    admin = svc.user(perms=["admin.access", "match.manage"])
    rooms = (await svc.client.get("/admin/v1/rooms", headers=admin.headers)).json()
    assert [r["code"] for r in rooms["items"]] == [code] and rooms["items"][0]["owner_epoch"]
    aborted = await svc.client.post(
        f"/admin/v1/rooms/{code}/abort",
        json={"reason": "зависшая комната"},
        headers={**admin.headers, "Idempotency-Key": "abort-0000001"},
    )
    assert aborted.status_code == 202, aborted.text
    gone = await svc.client.get(f"/v1/rooms/{code}", headers=a.headers)
    assert_problem(gone, 404, "match.room_not_found")


# ----------------------------------------------------------------------------- listen, artwork
async def test_duel_listen_position_and_reveal_artwork_without_leaks(app_factory: Any) -> None:
    svc = await app_factory(listen_per_player_per_min=5)
    svc.plans._factory = plan_factory(seed_bodies(), artwork=True)
    a, b, outsider = svc.user(), svc.user(), svc.user()
    code = (await svc.create(a, "music.duel.classic"))["code"]
    await svc.join(code, b)
    await _start(svc, a, code)

    # Соперник слушает: метка в состоянии и сообщении player; ответа нет.
    listened = await svc.ok(b, code, "listen", level=1, position_ms=2600)
    assert listened["version"] > 0
    seen = {p["user_id"]: p["listen_s"] for p in (await svc.state(a, code))["room"]["players"]}
    assert seen == {str(a.user_id): None, str(b.user_id): 2}
    again = await svc.ok(b, code, "listen", level=1, position_ms=2700)  # то же округление
    assert again["version"] == listened["version"]
    stale = await svc.ok(b, code, "listen", level=2, position_ms=1000)  # отстал на смене
    assert stale["version"] == listened["version"]
    # Не участник: ни ручки, ни состояния.
    assert_problem(
        await svc.post(outsider, code, "listen", level=1, position_ms=1),
        403,
        "match.not_participant",
    )
    assert (
        await svc.client.get(f"/v1/rooms/{code}/state", headers=outsider.headers)
    ).status_code == 403
    # Троттлинг: лимит позиций в минуту (настройка) - 429.
    await svc.ok(b, code, "listen", level=1, position_ms=3100)
    await svc.ok(b, code, "listen", level=1, position_ms=3900)
    assert_problem(
        await svc.post(b, code, "listen", level=1, position_ms=500), 429, "match.rate_limited"
    )

    # До ревила ни картинки, ни ключей ответа: ни в состоянии, ни в канале.
    before = await svc.state(a, code)
    assert_no_secrets(before, TITLES[:1])
    assert "artwork" not in json.dumps(before)
    assert not [x for x in svc.links.issued if x.subject.endswith(":reveal")]
    channel = _channel(svc, code)
    assert any(m["kind"] == "player" and m["delta"].get("listen_s") == 2 for m in channel)
    for message in channel:
        assert "artwork" not in json.dumps(message)
        assert_no_secrets(message, TITLES[:1])

    win = await svc.ok(a, code, "match/guess", **_guess(await svc.state(a, code), TITLES[0]))
    assert win["result"] == "exact"
    await svc.until(b, code, lambda s: s["match"]["level"] == 2)
    await svc.ok(b, code, "match/guess", **_guess(await svc.state(b, code), TITLES[0]))
    reveal = await svc.phase(a, code, "reveal")
    artwork = reveal["match"]["reveal"]["artwork"]
    assert set(artwork) == {"url", "expires_at", "mime"} and artwork["url"].startswith("/media/a/")
    delta = next(m for m in _channel(svc, code) if m["kind"] == "round_reveal")["delta"]
    assert {k: v for k, v in delta["artwork"].items() if k != "expires_at"} == {
        k: v for k, v in artwork.items() if k != "expires_at"
    }
    assert datetime.fromisoformat(delta["artwork"]["expires_at"]) == datetime.fromisoformat(
        artwork["expires_at"]
    )
    # Позиция прослушивания не живёт дольше уровня.
    assert all(p["listen_s"] is None for p in reveal["room"]["players"])
    svc.realtime.assert_versions_contiguous(f"room:{code}")
