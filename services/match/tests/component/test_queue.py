"""Очередь рейтинговой дуэли (4b, ADR 0038): enter/tick/leave/pool, окно, срыв отсчёта, итог.

Сервис целиком: api + процесс rooms с матчером, проекция rating доставляется событиями.
Время игроков (``entered_at``, окно) — ``FakeClock``; матчер просыпается по реальному таймеру.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from match_app import MatchApp, Player
from match_doubles import TITLES

from gv_auth import Sanction
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component


def _matched(ticket: dict[str, Any]) -> bool:
    return ticket["state"] == "matched"


async def _pair(svc: MatchApp, ra: int = 1000, rb: int = 1000) -> tuple[Player, Player, str]:
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.rate(a, "music", ra, 1)
    await svc.rate(b, "music", rb, 1)
    assert (await svc.enter(a)).status_code == 200
    assert (await svc.enter(b)).status_code == 200
    ticket = await svc.queue_state(a, _matched)
    svc.online.update((a.user_id, b.user_id))
    code = str(ticket["room_code"])
    await svc.phase(a, code, "countdown")  # комнату подхватил владелец и принял start
    return a, b, code


# ----------------------------------------------------------------------------- допуск
async def test_enter_unknown_ladder_and_unavailable_format(svc: MatchApp) -> None:
    player = svc.user()
    assert_problem(await svc.enter(player, "music"), 404, "match.ladder_not_found")
    await svc.ladder("music")
    # В проекции формат есть, но рейтингового шаблона lite для music среди сидов нет.
    assert_problem(await svc.enter(player, "music", "lite"), 422, "match.queue_format_unavailable")


async def test_ranked_sanction_is_forbidden_but_any_account_is_allowed(svc: MatchApp) -> None:
    await svc.ladder("music")
    banned = svc.user(sanctions=int(Sanction.RANKED))
    assert_problem(await svc.enter(banned), 403, "match.ranked_forbidden")
    # Дополнительных условий (возраст аккаунта, матчи, роли) нет.
    assert (await svc.enter(svc.user())).status_code == 200
    assert (await svc.enter(svc.user(sanctions=int(Sanction.CHAT_MUTE)))).status_code == 200


async def test_enter_is_idempotent_and_one_queue_per_player(svc: MatchApp) -> None:
    await svc.ladder("music")
    await svc.ladder("film")
    player = svc.user()
    first = await svc.enter(player)
    again = await svc.enter(player)
    assert first.status_code == again.status_code == 200
    assert first.json()["entered_at"] == again.json()["entered_at"]
    assert first.json() | {"wait_s": 0} == first.json()
    conflict = await svc.enter(player, "film")
    assert_problem(conflict, 409, "match.active_room_conflict")
    # Тот же Idempotency-Key с другим телом — конфликт ключа.
    key = {"Idempotency-Key": "queue-key-0001", **player.headers}
    body = {"ladder": "music", "format": "classic"}
    assert (await svc.client.post("/v1/queue/enter", json=body, headers=key)).status_code == 200
    other = await svc.client.post(
        "/v1/queue/enter", json={"ladder": "film", "format": "classic"}, headers=key
    )
    assert_problem(other, 409, "idempotency.key_reused")


async def test_player_with_active_room_cannot_enter(svc: MatchApp) -> None:
    await svc.ladder("music")
    host = svc.user()
    code = (await svc.create(host, "music.lobby.classic"))["code"]
    response = await svc.enter(host)
    assert_problem(response, 409, "match.active_room_conflict")
    assert response.json()["details"]["code"] == code


async def test_tick_leave_and_pool(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    assert_problem(await svc.queue_tick(player), 404, "match.not_in_queue")
    await svc.enter(player)
    ticket = (await svc.queue_tick(player)).json()
    assert ticket["state"] == "searching" and ticket["room_code"] is None
    assert ticket["window"] == 100 and ticket["slow"] is False
    pool = (await svc.client.get("/v1/queue/pool", headers=player.headers)).json()["items"]
    assert {"ladder": "music", "format": "classic", "searching": 1} in pool
    assert "user" not in json.dumps(pool)
    leave = {"Idempotency-Key": "leave-key-0001", **player.headers}
    assert (await svc.client.post("/v1/queue/leave", headers=leave)).status_code == 204
    assert (await svc.client.post("/v1/queue/leave", headers=leave)).status_code == 204
    assert_problem(await svc.queue_tick(player), 404, "match.not_in_queue")
    pool = (await svc.client.get("/v1/queue/pool", headers=player.headers)).json()["items"]
    assert {"ladder": "music", "format": "classic", "searching": 0} in pool


# ----------------------------------------------------------------------------- подбор
async def test_close_ratings_match_into_countdown_room_without_confirmation(
    svc: MatchApp,
) -> None:
    a, b, code = await _pair(svc, 1000, 1060)
    room = (await svc.state(a, code))["room"]
    assert room["phase"] == "countdown" and room["mode"]["ranked"] is True
    assert {p["user_id"] for p in room["players"]} == {str(a.user_id), str(b.user_id)}
    ticket_b = await svc.queue_state(b, _matched)
    assert ticket_b["room_code"] == code
    # Дуэль идёт обычным путём: после отсчёта открывается уровень.
    svc.clock.advance(seconds=3)
    await svc.phase(a, code, "level_open")


async def test_window_grows_with_waiting_time_and_slow_hint(svc: MatchApp) -> None:
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.rate(a, "music", 1000, 1)
    await svc.rate(b, "music", 1300, 1)
    await svc.enter(a)
    await svc.enter(b)
    # Разница 300 не входит в окно ±100: пара не собирается.
    svc.clock.advance(seconds=9)
    still = (await svc.queue_tick(a)).json()
    assert still["state"] == "searching" and still["window"] == 100
    # 30 с → ±250, 60 с → ±400: окно обоих достаёт, «ищем дольше обычного» уже горит.
    svc.clock.advance(seconds=21)
    assert (await svc.queue_tick(a)).json()["window"] == 250
    svc.clock.advance(seconds=30)
    ticket = await svc.queue_state(a, _matched)
    assert ticket["slow"] is True and ticket["window"] == 400


async def test_slow_hint_keeps_player_in_queue(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    await svc.enter(player)
    svc.clock.advance(seconds=61)
    ticket = (await svc.queue_tick(player)).json()
    assert ticket["slow"] is True and ticket["state"] == "searching"


async def test_newcomer_rating_comes_from_ladder_start(svc: MatchApp) -> None:
    await svc.ladder("music")
    newcomer, veteran = svc.user(), svc.user()
    await svc.rate(veteran, "music", 1040, 1)
    assert await svc.projected(newcomer, "music") is None
    await svc.enter(newcomer)  # 1000 (старт ladder-а) и 1040: в окне ±100
    await svc.enter(veteran)
    await svc.queue_state(newcomer, _matched)


async def test_pair_is_created_once_by_two_matchers(svc: MatchApp) -> None:
    from match_service.infrastructure.container import container_for

    a, b, code = await _pair(svc)
    runner = container_for(svc.infra, None).matchmaker_runner()
    assert await runner.pass_once() == 0  # второй матчер: очередь уже пуста
    keys = [k async for k in svc.redis.scan_iter("{room:*}:snap")]
    assert len(keys) == 1
    del a, b, code


# ----------------------------------------------------------------------------- срыв отсчёта
async def test_countdown_leave_requeues_opponent_without_penalty(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    before = (await svc.queue_tick(a)).json()
    await svc.ok(b, code, "leave")
    returned = await svc.queue_state(a, lambda t: t["state"] == "searching")
    assert returned["entered_at"] == before["entered_at"] and returned["room_code"] is None
    # Матча не было: ни MatchStarted, ни MatchFinished, комната закрыта.
    types = {e.event_type for e in await svc.outbox()}
    assert not types & {"match.MatchStarted", "match.MatchFinished"}
    assert (await svc.state(a, code))["room"]["phase"] == "closed"
    # Соперник возвращается в очередь и находит нового игрока.
    c = svc.user()
    await svc.enter(c)
    assert (await svc.queue_state(a, _matched))["room_code"] != code


async def test_leave_during_countdown_through_queue_api(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    leave = await svc.client.post(
        "/v1/queue/leave", headers={**b.headers, "Idempotency-Key": "leave-key-0002"}
    )
    assert leave.status_code == 204
    await svc.queue_state(a, lambda t: t["state"] == "searching")
    assert_problem(await svc.queue_tick(b), 404, "match.not_in_queue")
    del code


async def test_start_failure_closes_ranked_room_and_requeues_both(svc: MatchApp) -> None:
    from gv_core.errors import DependencyUnavailableError

    async def down(request: Any) -> Any:
        raise DependencyUnavailableError("game недоступен")

    svc.plans.create_round_plan = down  # type: ignore[method-assign]
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.enter(a)
    await svc.enter(b)
    await _wait_closed(svc)
    # Подбор состоялся, старт сорван: оба снова ищут, комната закрыта (без штрафа).
    for player in (a, b):
        await svc.queue_state(
            player, lambda t: t["state"] == "searching" and t["room_code"] is None
        )
    assert svc.plans is not None
    assert not {e.event_type for e in await svc.outbox()} & {"match.MatchStarted"}


# ----------------------------------------------------------------------------- рейтинговый матч
async def _start(svc: MatchApp, a: Player, code: str) -> None:
    svc.clock.advance(seconds=3)
    await svc.phase(a, code, "level_open")


async def _wait_closed(svc: MatchApp) -> None:
    """Ждать, пока матчер создал комнату и она закрылась из-за сорванного старта."""
    async with asyncio.timeout(8):
        while True:
            codes = [k async for k in svc.redis.scan_iter("{room:*}:snap")]
            if codes:
                raw = await svc.redis.get(codes[0])
                if raw is not None and b'"closed"' in raw:
                    return
            await asyncio.sleep(0.05)


async def _outbox(svc: MatchApp, event_type: str) -> list[Any]:
    async with asyncio.timeout(5):
        while True:
            found = [e for e in await svc.outbox() if e.event_type == event_type]
            if found:
                return found
            await asyncio.sleep(0.05)


async def test_ranked_events_carry_queue_format_and_leave_is_a_loss(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    await _start(svc, a, code)
    (started,) = await _outbox(svc, "match.MatchStarted")
    assert_event_matches_schema(started)
    assert started.payload["ranked"] is True and started.payload["ladder"] == "music"
    assert started.payload["queue_format"] == "classic"
    await svc.ok(b, code, "leave")
    await svc.phase(a, code, "summary")
    (finished,) = await _outbox(svc, "match.MatchFinished")
    assert_event_matches_schema(finished)
    assert finished.payload["queue_format"] == "classic" and finished.payload["aborted"] is False
    outcome = {p["user_id"]: (p["finish"], p["outcome"]) for p in finished.payload["players"]}
    assert outcome[str(b.user_id)][0] == "left" and outcome[str(b.user_id)][1] == "loss"
    assert outcome[str(a.user_id)][1] == "win"


async def test_ranked_forfeit_is_a_loss_and_rematch_is_forbidden(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    await _start(svc, a, code)
    await svc.ok(b, code, "match/forfeit")
    await svc.phase(a, code, "summary")
    (finished,) = await _outbox(svc, "match.MatchFinished")
    standings = {p["user_id"]: p for p in finished.payload["players"]}
    assert standings[str(b.user_id)]["finish"] == "forfeit"
    assert standings[str(b.user_id)]["outcome"] == "loss"
    response = await svc.post(a, code, "rematch")
    assert_problem(response, 403, "match.ranked_forbidden")


async def test_ranked_disconnect_past_grace_is_tech_loss(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    await _start(svc, a, code)
    svc.online.discard(b.user_id)
    # Пауза на двоих, затем grace (30 с шаблона): отключившийся — tech_loss.
    await svc.advance(code, [a], 130)
    await svc.advance(code, [a], 130)
    await svc.phase(a, code, "summary")
    (finished,) = await _outbox(svc, "match.MatchFinished")
    standings = {p["user_id"]: p for p in finished.payload["players"]}
    assert standings[str(b.user_id)]["finish"] == "tech_loss"
    assert standings[str(a.user_id)]["finish"] == "tech_win"


async def test_admin_abort_is_aborted_outcome(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    await _start(svc, a, code)
    admin = svc.user(perms=["admin.access", "match.manage"])
    response = await svc.client.post(
        f"/admin/v1/rooms/{code}/abort",
        json={"reason": "сбой сервера"},
        headers={**admin.headers, "Idempotency-Key": "abort-key-0001"},
    )
    assert response.status_code in (200, 202), response.text
    (finished,) = await _outbox(svc, "match.MatchFinished")
    assert finished.payload["aborted"] is True and finished.payload["queue_format"] == "classic"
    del b


async def test_custom_duel_has_no_queue_format(svc: MatchApp) -> None:
    host, guest = svc.user(), svc.user()
    code = (await svc.create(host, "music.duel.classic"))["code"]
    await svc.join(code, guest)
    room = (await svc.state(host, code))["room"]
    await svc.ok(host, code, "start", expected_version=room["version"])
    svc.clock.advance(seconds=3)
    await svc.phase(host, code, "level_open")
    (started,) = await _outbox(svc, "match.MatchStarted")
    assert started.payload["queue_format"] is None and started.payload["ranked"] is False


async def test_ranked_template_is_not_creatable_as_custom_room(svc: MatchApp) -> None:
    response = await svc.client.post(
        "/v1/rooms",
        json={"mode_key": "music.duel.ranked"},
        headers={**svc.user().headers, "Idempotency-Key": "create-key-0001"},
    )
    assert_problem(response, 403, "match.ranked_forbidden")


# ----------------------------------------------------------------------------- проекция rating
async def test_rating_projection_is_ordered_by_version_and_idempotent(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    assert await svc.rate(player, "music", 1100, 2) is Outcome.PROCESSED
    assert await svc.rate(player, "music", 900, 1) is Outcome.PROCESSED  # опоздавшее старое
    assert await svc.projected(player, "music") == 1100
    assert await svc.rate(player, "music", 1150, 3) is Outcome.PROCESSED
    assert await svc.projected(player, "music") == 1150


async def test_ladder_version_only_moves_forward(svc: MatchApp) -> None:
    from match_service.infrastructure.container import container_for

    await svc.ladder("music", version=2, base=150)
    await svc.ladder("music", version=1, base=10)
    ladders = container_for(svc.infra).ladders
    ladders.invalidate()
    info = await ladders.ladder("music")
    assert info is not None and info.version == 2 and info.matchmaking.base == 150


async def test_season_soft_reset_is_applied_by_projection(svc: MatchApp) -> None:
    await svc.ladder("music")
    high, low = svc.user(), svc.user()
    await svc.rate(high, "music", 1400, 1)
    await svc.rate(low, "music", 800, 1)
    season = {
        "ladder": "music",
        "season_id": "0192a3b4-c5d6-7e8f-9a0b-0000000a0002",
        "starts_at": "2026-10-23T00:00:00Z",
        "soft_reset": {"target": 1000, "ratio": 0.5},
    }
    first = await svc.rating_event("SeasonStarted", season["season_id"], **season)
    again = await svc.rating_event("SeasonStarted", season["season_id"], **season)
    assert first is Outcome.PROCESSED and again is Outcome.PROCESSED
    assert await svc.projected(high, "music") == 1200
    assert await svc.projected(low, "music") == 900  # повтор события сброс не удваивает
    # Матч нового сезона: значение не сбрасывается повторно.
    await svc.rate(high, "music", 1230, 2, season_id=season["season_id"], at="2026-10-24T00:00:00Z")
    assert await svc.projected(high, "music") == 1230


async def test_late_rating_of_previous_season_is_reset_too(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    season = {
        "ladder": "music",
        "season_id": "0192a3b4-c5d6-7e8f-9a0b-0000000a0002",
        "starts_at": "2026-10-23T00:00:00Z",
        "soft_reset": {"target": 1000, "ratio": 0.5},
    }
    await svc.rating_event("SeasonStarted", season["season_id"], **season)
    # RatingChanged старого сезона приехал после старта нового (разные ключи партиций).
    await svc.rate(player, "music", 1400, 5, at="2026-10-22T20:00:00Z")
    assert await svc.projected(player, "music") == 1200


async def test_deleted_account_rating_is_forgotten(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    await svc.rate(player, "music", 1100, 1)
    outcome = await svc.rating_event_of(
        "identity.UserDeleted", str(player.user_id), user_id=str(player.user_id), version=9
    )
    assert outcome is Outcome.PROCESSED
    assert await svc.projected(player, "music") is None


# ----------------------------------------------------------------------------- наблюдение
async def test_spectate_needs_permission_and_hides_lobbies(svc: MatchApp) -> None:
    plain = svc.user()
    assert_problem(
        await svc.client.get("/v1/rooms/spectate", headers=plain.headers), 403, "auth.forbidden"
    )
    watcher = svc.user(perms=["match.spectate"])
    lobby_host = svc.user()
    await svc.create(lobby_host, "music.lobby.classic")
    page = (await svc.client.get("/v1/rooms/spectate", headers=watcher.headers)).json()
    assert page["items"] == []  # лобби (код приватный) наблюдателю не показывается


async def test_spectator_sees_running_match_read_only_without_secrets(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    await _start(svc, a, code)
    watcher = svc.user(perms=["match.spectate"])
    page = (await svc.client.get("/v1/rooms/spectate", headers=watcher.headers)).json()
    (card,) = page["items"]
    assert card["code"] == code and card["players"] == 2 and card["mode"]["ranked"] is True
    state = await svc.client.get(f"/v1/rooms/{code}/state", headers=watcher.headers)
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["me"] is None and body["match"] is not None
    text = json.dumps(body, ensure_ascii=False)
    for title in TITLES[:3]:
        assert title not in text  # ответ раунда до ревила не виден
    for forbidden in ('"media_item_id"', '"aliases"', '"asset_set_id"', '"answer"', '"reveal": {'):
        assert forbidden not in text
    token = await svc.client.post(f"/v1/rooms/{code}/realtime-token", headers=watcher.headers)
    assert token.status_code == 200 and token.json()["channel"] == f"room:{code}"
    # Только чтение: наблюдатель не участник — команды комнаты ему недоступны.
    guess = await svc.post(
        watcher,
        code,
        "match/guess",
        expected_version=body["room"]["version"],
        round_index=0,
        level=1,
        answer={"text": TITLES[0]},
    )
    assert_problem(guess, 403, "match.not_participant")
    sneaky = await svc.post(watcher, code, "match/forfeit")
    assert_problem(sneaky, 403, "match.not_participant")
    del b


async def test_non_spectator_cannot_read_foreign_room(svc: MatchApp) -> None:
    a, b, code = await _pair(svc)
    stranger = svc.user()
    assert_problem(
        await svc.client.get(f"/v1/rooms/{code}/state", headers=stranger.headers),
        403,
        "match.not_participant",
    )
    assert_problem(
        await svc.client.post(f"/v1/rooms/{code}/realtime-token", headers=stranger.headers),
        403,
        "match.not_participant",
    )
    del a, b


async def test_spectate_is_rate_limited_and_guest_forbidden(app_factory: Any) -> None:
    svc: MatchApp = await app_factory(spectate_per_user_per_min=2)
    watcher = svc.user(perms=["match.spectate"])
    for _ in range(2):
        assert (
            await svc.client.get("/v1/rooms/spectate", headers=watcher.headers)
        ).status_code == 200
    assert_problem(
        await svc.client.get("/v1/rooms/spectate", headers=watcher.headers),
        429,
        "match.rate_limited",
    )
    guest = svc.guest()
    assert (await svc.client.get("/v1/rooms/spectate", headers=guest.headers)).status_code == 403
    bad = await svc.client.get(
        "/v1/rooms/spectate?cursor=zz", headers=svc.user(perms=["match.spectate"]).headers
    )
    assert_problem(bad, 422, "match.cursor_invalid")


async def test_ticket_carries_heartbeat_period_for_the_client(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    entered = (await svc.enter(player)).json()
    ticked = (await svc.queue_tick(player)).json()
    tick_s = svc.infra.settings.match.tick_s  # type: ignore[attr-defined]  # настройки match
    assert entered["tick_s"] == ticked["tick_s"] == tick_s


# ------------------------------------------------------------------ цикл start_failed (п. 23)
async def _closed_rooms(svc: MatchApp) -> int:
    """Сколько закрытых комнат уже создал матчер."""
    closed = 0
    async for key in svc.redis.scan_iter("{room:*}:snap"):
        raw = await svc.redis.get(key)
        closed += int(raw is not None and b'"closed"' in raw)
    return closed


async def _wait_closed_rooms(svc: MatchApp, count: int) -> None:
    async with asyncio.timeout(8):
        while await _closed_rooms(svc) < count:  # noqa: ASYNC110 - опрос Redis, события нет
            await asyncio.sleep(0.05)


async def test_start_failure_backs_off_instead_of_a_room_per_second(app_factory: Any) -> None:
    """Сорванный старт: пауза с нарастанием, новая комната не каждую секунду."""
    from gv_core.errors import DependencyUnavailableError

    svc: MatchApp = await app_factory(
        start_failed_backoff_s=10, start_failed_backoff_max_s=60, start_failed_max_attempts=5
    )

    async def down(request: Any) -> Any:
        raise DependencyUnavailableError("game недоступен")

    svc.plans.create_round_plan = down  # type: ignore[method-assign]
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.enter(a)
    await svc.enter(b)
    await _wait_closed_rooms(svc, 1)
    for _ in range(4):  # 8 с фейкового времени < паузы 10 с: нового подбора быть не должно
        svc.clock.advance(seconds=2)
        await asyncio.sleep(0.4)
    assert await _closed_rooms(svc) == 1
    for player in (a, b):  # игроки при этом всё ещё в поиске
        await svc.queue_state(player, lambda t: t["state"] == "searching")
    svc.clock.advance(seconds=3)  # пауза прошла - вторая попытка
    await _wait_closed_rooms(svc, 2)
    for _ in range(4):  # вторая пауза вдвое длиннее (20 с): за 8 с третьей комнаты нет
        svc.clock.advance(seconds=2)
        await asyncio.sleep(0.4)
    assert await _closed_rooms(svc) == 2


async def test_start_failure_gives_up_after_limit_with_clear_error(app_factory: Any) -> None:
    from gv_core.errors import DependencyUnavailableError

    svc: MatchApp = await app_factory(
        start_failed_backoff_s=1, start_failed_backoff_max_s=1, start_failed_max_attempts=2
    )

    async def down(request: Any) -> Any:
        raise DependencyUnavailableError("game недоступен")

    svc.plans.create_round_plan = down  # type: ignore[method-assign]
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.enter(a)
    await svc.enter(b)
    await _wait_closed_rooms(svc, 1)
    svc.clock.advance(seconds=2)
    await _wait_closed_rooms(svc, 2)
    for player in (a, b):  # лимит исчерпан: понятная ошибка, а не вечный поиск
        problem = None
        async with asyncio.timeout(6):
            while problem is None:
                response = await svc.queue_tick(player)
                if response.status_code != 200:
                    problem = response
                await asyncio.sleep(0.05)
        assert_problem(problem, 503, "match.start_unavailable")
        assert_problem(
            await svc.queue_tick(player), 404, "match.not_in_queue"
        )  # сообщение один раз
    svc.clock.advance(seconds=30)
    await asyncio.sleep(1.0)
    assert await _closed_rooms(svc) == 2  # комнат больше нет: цикл остановлен


async def test_new_search_clears_stale_start_unavailable_notice(svc: MatchApp) -> None:
    await svc.ladder("music")
    a = svc.user()
    from match_service.infrastructure.container import container_for

    container = container_for(svc.infra, None)
    await container.ranked_queue.fail(a.user_id, "match.start_unavailable", 60)
    assert (await svc.enter(a)).status_code == 200
    await svc.queue_tick(a)
    await svc.client.post("/v1/queue/leave", headers={**a.headers, "Idempotency-Key": a.op()})
    assert_problem(await svc.queue_tick(a), 404, "match.not_in_queue")  # а не устаревшее 503
