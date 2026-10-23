"""Очередь подбора: сбои матчера, откат, зависший резерв, проекция и наблюдатели (ревью Q).

Сбои подставляются в адаптеры контейнера (шина, хранилище комнат): матчер должен вернуть пару
в поиск под актуальным кодом комнаты и не оставить недозапущенную комнату.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest
from match_app import MatchApp, Player
from match_service.domain.errors import MatchUnavailableError
from match_service.infrastructure.container import container_for
from match_service.infrastructure.messaging import UNKNOWN_FORMATS
from match_service.infrastructure.rooms import keys

from gv_messaging import Outcome
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

SEASON = {
    "ladder": "music",
    "season_id": "0192a3b4-c5d6-7e8f-9a0b-0000000a0002",
    "starts_at": "2026-10-23T00:00:00Z",
    "soft_reset": {"target": 1100, "ratio": 0.5},
}


def _matched(ticket: dict[str, Any]) -> bool:
    return ticket["state"] == "matched"


def _searching(ticket: dict[str, Any]) -> bool:
    return ticket["state"] == "searching"


async def _two(svc: MatchApp) -> tuple[Player, Player]:
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.enter(a)
    await svc.enter(b)
    return a, b


async def _snapshots(svc: MatchApp) -> list[str]:
    return [k.decode() async for k in svc.redis.scan_iter("{room:*}:snap")]


async def _until(check: Callable[[], Any], timeout: float = 8) -> None:  # noqa: ASYNC109
    """Опрос условия (обычного или async) с пределом ожидания."""
    async with asyncio.timeout(timeout):
        while True:
            result = check()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return
            await asyncio.sleep(0.05)


# ----------------------------------------------------------------------------- К1: откат
async def test_start_command_is_retried_once_when_bus_blips(
    svc: MatchApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = container_for(svc.infra).bus
    real, calls = bus.send, []

    async def flaky(code: str, command: Any) -> None:
        calls.append(code)
        if len(calls) == 1:
            raise ConnectionError("redis моргнул")
        await real(code, command)

    monkeypatch.setattr(bus, "send", flaky)
    a, _ = await _two(svc)
    ticket = await svc.queue_state(a, _matched)
    await svc.phase(a, ticket["room_code"], "countdown")
    assert calls[0] == calls[1] == ticket["room_code"]  # тот же код: повтор, а не новая комната


async def test_failed_start_discards_room_and_requeues_pair_under_current_code(
    svc: MatchApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = container_for(svc.infra).bus
    real, failed = bus.send, []
    broken = {"on": True}

    async def send(code: str, command: Any) -> None:
        if broken["on"] and command.kind == "start":
            failed.append(code)
            raise ConnectionError("шина недоступна")
        await real(code, command)

    monkeypatch.setattr(bus, "send", send)
    a, b = await _two(svc)

    async def attempted() -> bool:
        return len(failed) >= 2  # попытка + повтор

    await _until(attempted)
    first_code = failed[0]

    async def discarded() -> bool:
        return await svc.redis.get(keys.snapshot(first_code)) is None

    await _until(discarded)
    for player in (a, b):
        ticket = await svc.queue_state(player, _searching)
        assert ticket["room_code"] is None
    broken["on"] = False
    final = await svc.queue_state(a, _matched)
    assert final["room_code"] != first_code
    assert not [k for k in await _snapshots(svc) if first_code in k]


async def test_store_create_failure_returns_pair_to_search(
    svc: MatchApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = container_for(svc.infra).store
    real, calls = store.create, []

    async def create(room: Any) -> bool:
        calls.append(room.code)
        if len(calls) == 1:
            raise MatchUnavailableError("redis недоступен")
        return await real(room)

    monkeypatch.setattr(store, "create", create)
    a, b = await _two(svc)
    ticket = await svc.queue_state(a, _matched)
    assert ticket["room_code"] == calls[-1] and ticket["room_code"] != calls[0]
    assert (await svc.queue_state(b, _matched))["room_code"] == calls[-1]


async def test_room_code_collision_moves_pointers_and_records_to_the_new_code(
    svc: MatchApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = container_for(svc.infra).store
    real, codes = store.create, []

    async def create(room: Any) -> bool:
        codes.append(room.code)
        return False if len(codes) == 1 else await real(room)

    monkeypatch.setattr(store, "create", create)
    a, b = await _two(svc)
    ticket = await svc.queue_state(a, _matched)
    code = ticket["room_code"]
    assert code == codes[1] != codes[0]
    await svc.phase(a, code, "countdown")
    rooms = container_for(svc.infra).player_rooms
    for player in (a, b):
        assert await rooms.get(player.user_id) == code


async def test_player_busy_in_another_room_is_dropped_and_opponent_keeps_searching(
    svc: MatchApp,
) -> None:
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.enter(a)
    lobby = (await svc.create(a, "music.lobby.classic"))["code"]  # a занял комнату в очереди
    await svc.enter(b)

    async def dropped() -> bool:
        return (await svc.queue_tick(a)).status_code == 404

    await _until(dropped)
    assert (await svc.queue_state(b, _searching))["room_code"] is None
    assert await _snapshots(svc) == [keys.snapshot(lobby)]  # рейтинговой комнаты нет
    assert await container_for(svc.infra).player_rooms.get(a.user_id) == lobby


# ----------------------------------------------------------------------------- К2, К3: резерв
async def _crash_after_reserve(svc: MatchApp, monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Первый подбор «падает» сразу после резерва пары (комнаты нет, подтверждения нет)."""
    matcher = container_for(svc.infra).matchmaker
    real, state = matcher._match, {"crashed": False}

    async def crash(first: Any, second: Any, tpl: Any, fence: Any) -> bool:
        if state["crashed"]:
            return await real(first, second, tpl, fence)
        state["crashed"] = True
        queue = container_for(svc.infra).ranked_queue
        await queue.reserve(fence, first.user_id, second.user_id, "CRASHED2", matcher._now_ms())
        return False

    monkeypatch.setattr(matcher, "_match", crash)
    return state


async def test_stale_reserve_after_matcher_crash_returns_players_to_search(
    svc: MatchApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _crash_after_reserve(svc, monkeypatch)
    a, b = await _two(svc)
    await _until(lambda: state["crashed"])
    # Резерв виден клиенту как поиск; tick продлевает запись.
    assert (await svc.queue_tick(a)).json()["state"] == "searching"
    svc.clock.advance(seconds=11)  # старше GV_MATCH__QUEUE_RESERVED_STALE_S
    ticket = await svc.queue_state(a, _matched)
    assert ticket["room_code"] != "CRASHED2"
    assert (await svc.queue_state(b, _matched))["room_code"] == ticket["room_code"]


async def test_leave_while_pair_is_reserved_conflicts_and_keeps_the_record(
    svc: MatchApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _crash_after_reserve(svc, monkeypatch)
    a, _ = await _two(svc)
    await _until(lambda: state["crashed"])
    leave = await svc.client.post(
        "/v1/queue/leave", headers={**a.headers, "Idempotency-Key": "leave-key-0003"}
    )
    assert_problem(leave, 409, "match.queue_matching")
    assert (await svc.queue_tick(a)).status_code == 200  # запись цела


# ----------------------------------------------------------------------------- К4: сезон и ladder
async def test_season_before_ladder_keeps_soft_reset_and_start_rating(svc: MatchApp) -> None:
    player = svc.user()
    await svc.rate(player, "music", 1400, 1)
    # SeasonStarted пришёл раньше LadderPublished (потоки разных ключей не упорядочены).
    for _ in range(2):  # повтор с новым event_id сброс не удваивает
        outcome = await svc.rating_event("SeasonStarted", SEASON["season_id"], **SEASON)
        assert outcome is Outcome.PROCESSED
    assert await svc.projected(player, "music") == 1250  # 1100 + 0.5 × (1400 − 1100)
    await svc.ladder("music")
    info = await container_for(svc.infra).ladders.ladder("music")
    assert info is not None and info.start_rating == 1100
    assert await svc.projected(player, "music") == 1250


async def test_season_after_ladder_gives_same_result(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    await svc.rate(player, "music", 1400, 1)
    await svc.rating_event("SeasonStarted", SEASON["season_id"], **SEASON)
    await svc.rating_event("SeasonStarted", SEASON["season_id"], **SEASON)
    assert await svc.projected(player, "music") == 1250
    info = await container_for(svc.infra).ladders.ladder("music")
    assert info is not None and info.start_rating == 1100


async def test_invalid_ladder_key_is_dead_lettered_and_unknown_formats_are_counted(
    svc: MatchApp,
) -> None:
    bad = await svc.rating_event("LadderPublished", "Bad Key", ladder="Bad Key", version=1)
    assert bad is Outcome.DEAD
    before = UNKNOWN_FORMATS._value.get()
    ok = await svc.rating_event(
        "LadderPublished", "music", ladder="music", domain="music", formats=["classic", "weird"]
    )
    assert ok is Outcome.PROCESSED and UNKNOWN_FORMATS._value.get() == before + 1


# ----------------------------------------------------------------------------- Б1: удалённый игрок
async def test_rating_event_after_account_deletion_is_ignored(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    deleted = await svc.rating_event_of(
        "identity.UserDeleted", str(player.user_id), user_id=str(player.user_id), version=9
    )
    assert deleted is Outcome.PROCESSED
    assert await svc.rate(player, "music", 1500, 1) is Outcome.PROCESSED
    assert await svc.projected(player, "music") is None  # рейтинг не воскресает


async def test_account_deletion_removes_player_from_queue(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    await svc.enter(player)
    await svc.rating_event_of(
        "identity.UserDeleted", str(player.user_id), user_id=str(player.user_id), version=9
    )
    assert_problem(await svc.queue_tick(player), 404, "match.not_in_queue")
    assert await container_for(svc.infra).ranked_queue.count("music", "classic") == 0


# ----------------------------------------------------------------------------- Б2: наблюдатели
async def test_spectator_subscription_is_revoked_when_match_ends(svc: MatchApp) -> None:
    await svc.ladder("music")
    a, b = svc.user(), svc.user()
    await svc.enter(a)
    await svc.enter(b)
    code = (await svc.queue_state(a, _matched))["room_code"]
    svc.online.update((a.user_id, b.user_id))
    svc.clock.advance(seconds=3)
    await svc.phase(a, code, "level_open")
    watcher = svc.user(perms=["match.spectate"])
    token = await svc.client.post(f"/v1/rooms/{code}/realtime-token", headers=watcher.headers)
    assert token.status_code == 200
    await svc.ok(b, code, "match/forfeit")
    await svc.phase(a, code, "summary")

    async def revoked() -> bool:
        return (watcher.user_id, f"room:{code}") in svc.subscriptions.revoked

    await _until(revoked)
    # Итог наблюдателю больше недоступен.
    again = await svc.client.get(f"/v1/rooms/{code}/state", headers=watcher.headers)
    assert_problem(again, 404, "match.room_not_found")


# ----------------------------------------------------------------------------- прочее
async def test_ranked_sanction_issued_while_queued_is_rechecked_on_tick(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    await svc.enter(player)
    banned = svc.issuer.issue(user_id=player.user_id, sanctions=1)
    response = await svc.client.post(
        "/v1/queue/tick", headers={"Authorization": f"Bearer {banned}"}
    )
    assert_problem(response, 403, "match.ranked_forbidden")
    assert_problem(await svc.queue_tick(player), 404, "match.not_in_queue")
