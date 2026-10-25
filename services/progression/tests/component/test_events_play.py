"""Сезонные события для игрока: страница, claim, серверные триггеры, tick, награды (5d)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.accrual.posting import Poster
from sqlalchemy import text
from structlog.testing import capture_logs

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, Outcome
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
KEY = "autumn"


def fresh(progression: Service, env: EventEnvelope) -> EventEnvelope:
    """Событие с новым ``event_id`` и временем «сейчас» (как пришедшее от сервиса)."""
    return env.model_copy(update={"event_id": uuid7(), "occurred_at": progression.clock.now()})


async def deliver(progression: Service, env: EventEnvelope) -> None:
    assert await progression.deliver(fresh(progression, env)) is Outcome.PROCESSED


async def page(progression: Service, user: UUID) -> dict[str, Any]:
    response = await progression.get(f"/v1/events/{KEY}", user)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def finds_by_key(data: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: f for f in data["finds"]}


async def start_event(
    progression: Service, *finds: dict[str, Any], item: str | None = None
) -> UUID:
    """Конфиг (с историей до начала события), определение и идущий запуск."""
    await progression.publish_config(at=progression.clock.now() - timedelta(days=60))
    if item is not None:
        await progression.add_item(item, event_key=KEY)
    return await progression.publish_event(KEY, progression.ev.body(*finds, item_key=item))


async def test_active_banner_and_page_visibility(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    anonymous = await progression.client.get("/v1/events/active")
    assert anonymous.status_code == 200 and anonymous.json() == {"event": None}

    await progression.create_event(KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)))
    user = uuid7()
    assert_problem(
        await progression.get(f"/v1/events/{KEY}", user), 404, "progression.event_not_found"
    )
    run = await progression.publish_event(
        KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)), create=False
    )

    banner = (await progression.client.get("/v1/events/active")).json()["event"]
    assert banner["run_id"] == str(run) and banner["theme_key"] == "autumn"
    data = await page(progression, user)
    assert data["status"] == "active" and data["total"] == 2 and data["found"] == 0
    leaf, fin = finds_by_key(data)["leaf"], finds_by_key(data)["fin"]
    assert leaf["state"] == "open" and leaf["diary"] == "до leaf"
    assert fin["state"] == "locked" and fin["trigger"]["progress"] is None


async def test_event_pages_need_an_account(progression: Service) -> None:
    await progression.publish_config()
    assert_problem(await progression.client.get(f"/v1/events/{KEY}"), 401, "auth.unauthenticated")
    assert_problem(await progression.client.get("/v1/me/events"), 401, "auth.unauthenticated")


async def test_claim_flow_rewards_once_and_completes_with_item(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("leaf", 1, xp=10, verses=5),
        ev.find("fin", 2, final=True, xp=20, verses=7),
        item="autumn_frame",
    )
    user = uuid7()

    locked = await progression.claim(user, KEY, "fin")
    assert_problem(locked, 409, "progression.event_find_locked")
    assert await progression.xp_rows(user) == 0

    first = await progression.claim(user, KEY, "leaf")
    assert first.status_code == 200 and first.headers["cache-control"] == "private, no-store"
    result = first.json()
    assert result["newly_unlocked"] is True and result["found"] == 1 and not result["completed"]
    assert result["find"]["state"] == "found" and result["find"]["diary"] == "после leaf"
    assert await progression.xp_total(user) == 10 and await progression.balance(user) == 5

    repeat = (await progression.claim(user, KEY, "leaf")).json()
    assert repeat["newly_unlocked"] is False and repeat["found"] == 1
    assert await progression.xp_rows(user) == 1 and await progression.verse_rows(user) == 1

    final = (await progression.claim(user, KEY, "fin")).json()
    assert final["completed"] is True and final["found"] == 2
    assert await progression.xp_total(user) == 30 and await progression.balance(user) == 12

    inventory = (await progression.get("/v1/me/inventory", user)).json()["items"]
    assert [(i["item"]["key"], i["source"]) for i in inventory] == [("autumn_frame", "event")]
    unlocked = await progression.outbox("progression.FindUnlocked")
    assert [(e.payload["find_key"], e.payload["found"]) for e in unlocked] == [
        ("leaf", 1),
        ("fin", 2),
    ]
    completed = await progression.outbox("progression.EventCompleted")
    assert len(completed) == 1 and completed[0].payload["item_key"] == "autumn_frame"
    assert len(await progression.rows("event_completions", user)) == 1

    await progression.claim(user, KEY, "fin")
    assert len(await progression.outbox("progression.EventCompleted")) == 1
    assert len(await progression.rows("accrual_marks", user)) == 1

    archive = (await progression.get("/v1/me/events", user)).json()["items"]
    assert [(a["key"], a["found"], a["total"], a["completed"]) for a in archive] == [
        (KEY, 2, 2, True)
    ]
    assert (await progression.get("/v1/me/events", uuid7())).json() == {"items": []}


async def test_owned_event_item_is_not_granted_twice_but_event_still_completes(
    progression: Service,
) -> None:
    ev = progression.ev
    await start_event(
        progression, ev.find("a", 1), ev.find("fin", 2, final=True), item="autumn_frame"
    )
    user = uuid7()
    await progression.claim(user, KEY, "a")
    await progression.claim(user, KEY, "fin")
    inventory = (await progression.get("/v1/me/inventory", user)).json()["items"]
    assert len(inventory) == 1


async def test_claim_errors(progression: Service) -> None:
    ev = progression.ev
    run = await start_event(
        progression,
        ev.find("leaf", 1),
        ev.find("srv", 2, ev.trigger("rounds_played", 3)),
        ev.find("fin", 3, final=True),
    )
    user = uuid7()
    assert_problem(
        await progression.claim(user, KEY, "nope"), 404, "progression.event_find_not_found"
    )
    assert_problem(
        await progression.claim(user, "other", "leaf"), 404, "progression.event_not_found"
    )
    assert_problem(
        await progression.claim(user, KEY, "srv"), 409, "progression.event_find_not_ui_spot"
    )
    await progression.client.post(
        f"/admin/v1/events/{KEY}/runs/{run}/stop",
        headers=progression.admin(ev.PUBLISH),
        json={"reason": "тест остановки"},
    )
    assert_problem(await progression.claim(user, KEY, "leaf"), 409, "progression.event_not_active")
    # страница остановленного события остаётся доступной (архив)
    assert (await page(progression, user))["status"] == "stopped"


async def test_claim_is_rate_limited_per_player(progression: Service) -> None:
    ev = progression.ev
    await start_event(progression, ev.find("leaf", 1), ev.find("fin", 2, final=True))
    limit, _ = progression.container.settings.progression.event_claim_rate
    user = uuid7()
    for _ in range(limit):
        assert (await progression.claim(user, KEY, "leaf")).status_code == 200
    blocked = await progression.claim(user, KEY, "leaf")
    assert_problem(blocked, 429, "progression.event_claim_rate_limited")
    assert int(blocked.headers["retry-after"]) >= 1
    assert (await progression.claim(uuid7(), KEY, "leaf")).status_code == 200


async def test_claim_fails_closed_when_limiter_is_down(progression: Service) -> None:
    ev = progression.ev
    await start_event(progression, ev.find("leaf", 1), ev.find("fin", 2, final=True))

    class Down:
        async def hit(self, key: str, window_s: int) -> Any:
            raise DependencyUnavailableError("redis недоступен")

    progression.container.limiter = Down()
    user = uuid7()
    assert (await progression.claim(user, KEY, "leaf")).status_code == 503
    assert await progression.xp_rows(user) == 0


# ------------------------------------------------------------------ серверные триггеры
async def test_counter_trigger_accumulates_and_event_resend_does_not_double(
    progression: Service,
) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("rounds", 1, ev.trigger("rounds_played", 2), xp=10, verses=5),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    session = uuid7()
    first = progression.game_finished(user, [10], session_id=session)
    await deliver(progression, first)
    # то же событие предмета с новым event_id: метрики не двоятся
    await deliver(progression, first)
    data = finds_by_key(await page(progression, user))
    assert data["rounds"]["trigger"]["progress"] == 1 and data["rounds"]["state"] == "open"

    await deliver(progression, progression.game_finished(user, [10]))
    data = finds_by_key(await page(progression, user))
    assert data["rounds"]["state"] == "found" and data["rounds"]["trigger"]["progress"] == 2
    ledger = [r for r in await progression.rows("xp_ledger", user) if r.reason == "event"]
    assert len(ledger) == 1 and ledger[0].op_id.startswith("event:")
    # вернувшееся после открытия событие ничего не добавляет
    await deliver(progression, progression.game_finished(user, [10]))
    ledger = [r for r in await progression.rows("xp_ledger", user) if r.reason == "event"]
    assert len(ledger) == 1
    assert len(await progression.outbox("progression.FindUnlocked")) == 1


async def test_events_outside_run_period_do_not_count(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("rounds", 1, ev.trigger("rounds_played", 1)),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    before = progression.clock.now() - timedelta(days=3)  # запуск начался сутки назад
    await deliver(progression, progression.game_finished(user, [10], finished_at=before))
    assert finds_by_key(await page(progression, user))["rounds"]["trigger"]["progress"] == 0
    after = progression.clock.now() + timedelta(days=8)  # запуск закончился
    await deliver(progression, progression.game_finished(user, [10], finished_at=after))
    assert finds_by_key(await page(progression, user))["rounds"]["state"] == "open"
    assert len(await progression.outbox("progression.FindUnlocked")) == 0


async def test_day_period_resets_every_day(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("daily", 1, ev.trigger("rounds_played", 2, "day")),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    await deliver(progression, progression.game_finished(user, [10]))
    progression.clock.advance(days=1)
    await deliver(progression, progression.game_finished(user, [10]))
    assert finds_by_key(await page(progression, user))["daily"]["trigger"]["progress"] == 1
    await deliver(progression, progression.game_finished(user, [10]))
    assert finds_by_key(await page(progression, user))["daily"]["state"] == "found"


async def test_duel_win_counts_only_winner_of_duel(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression, ev.find("duel", 1, ev.trigger("duel_win")), ev.find("fin", 2, final=True)
    )
    winner, loser = uuid7(), uuid7()
    await deliver(progression, progression.duel(winner, loser))
    assert finds_by_key(await page(progression, winner))["duel"]["state"] == "found"
    assert finds_by_key(await page(progression, loser))["duel"]["state"] == "open"


async def test_weekly_top_place_threshold(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("top", 1, ev.trigger("weekly_top_place", 1, place_max=3)),
        ev.find("fin", 2, final=True),
    )
    high, low = uuid7(), uuid7()
    await deliver(progression, progression.weekly_top([(high, 2), (low, 5)]))
    assert finds_by_key(await page(progression, high))["top"]["state"] == "found"
    assert finds_by_key(await page(progression, low))["top"]["state"] == "open"


async def test_collection_played_needs_collection_id(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("coll", 1, ev.trigger("collection_played")),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    plain = progression.game_finished(user, [10])
    plain.payload["collection_id"] = None
    await deliver(progression, plain)
    assert finds_by_key(await page(progression, user))["coll"]["state"] == "open"
    with_collection = progression.game_finished(user, [10])
    with_collection.payload["collection_id"] = str(uuid7())
    await deliver(progression, with_collection)
    assert finds_by_key(await page(progression, user))["coll"]["state"] == "found"


async def test_collection_keys_count_only_listed_collections(progression: Service) -> None:
    ev = progression.ev
    wanted, other = uuid7(), uuid7()
    await start_event(
        progression,
        ev.find("coll", 1, ev.trigger("collection_played", collection_keys=[str(wanted)])),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    foreign = progression.game_finished(user, [10])
    foreign.payload["collection_id"] = str(other)
    await deliver(progression, foreign)
    assert finds_by_key(await page(progression, user))["coll"]["state"] == "open"
    listed = progression.game_finished(user, [10])
    listed.payload["collection_id"] = str(wanted)
    await deliver(progression, listed)
    assert finds_by_key(await page(progression, user))["coll"]["state"] == "found"


async def test_wheel_spin_streak_needs_consecutive_days(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("streak", 1, ev.trigger("wheel_spin_streak", 2)),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    assert (await progression.spin(user)).status_code == 200
    assert finds_by_key(await page(progression, user))["streak"]["trigger"]["progress"] == 1
    progression.clock.advance(days=2)  # пропуск суток обнуляет серию
    assert (await progression.spin(user)).status_code == 200
    assert finds_by_key(await page(progression, user))["streak"]["state"] == "open"
    progression.clock.advance(days=1)
    assert (await progression.spin(user)).status_code == 200
    assert finds_by_key(await page(progression, user))["streak"]["state"] == "found"


async def test_item_equipped_by_slot(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("wear", 1, ev.trigger("item_equipped", 1, slot="frame")),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    item = await progression.add_item("f1", slot="frame", price=10)
    await progression.grant_verses(user, 10)
    assert (await progression.purchase(user, item)).status_code == 200
    assert finds_by_key(await page(progression, user))["wear"]["state"] == "open"
    equip = await progression.client.put(
        "/v1/me/appearance/frame", json={"item_id": str(item)}, headers=progression.user(user)
    )
    assert equip.status_code == 200
    assert finds_by_key(await page(progression, user))["wear"]["state"] == "found"


async def test_final_server_find_unlocks_after_the_others(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression,
        ev.find("leaf", 1),
        ev.find("fin", 2, ev.trigger("rounds_played", 1), final=True),
    )
    user = uuid7()
    await deliver(progression, progression.game_finished(user, [10]))
    data = finds_by_key(await page(progression, user))
    assert data["fin"]["state"] == "locked" and data["fin"]["trigger"]["progress"] is None
    assert len(await progression.outbox("progression.FindUnlocked")) == 0

    result = (await progression.claim(user, KEY, "leaf")).json()
    assert result["completed"] is True and result["found"] == 2
    assert len(await progression.outbox("progression.FindUnlocked")) == 2


# ------------------------------------------------------------------ tick
async def test_tick_materializes_status_and_publishes_once(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    now = progression.clock.now()
    await progression.publish_event(
        KEY,
        ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)),
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=3),
    )
    user = uuid7()
    await progression.run_job("progression.events.tick")
    assert await progression.outbox("progression.EventRunStarted") == []
    assert_problem(
        await progression.get(f"/v1/events/{KEY}", user), 404, "progression.event_not_found"
    )

    progression.clock.advance(hours=2)
    await progression.run_job("progression.events.tick")
    await progression.run_job("progression.events.tick")
    started = await progression.outbox("progression.EventRunStarted")
    assert len(started) == 1 and started[0].payload["event_key"] == KEY
    assert (await progression.client.get("/v1/events/active")).json()["event"]["key"] == KEY
    assert await progression.outbox("progression.EventRunFinished") == []

    progression.clock.advance(hours=2)
    # чтение не ждёт задачу: статус уже считается по времени
    assert (await page(progression, user))["status"] == "finished"
    assert (await progression.client.get("/v1/events/active")).json() == {"event": None}
    await progression.run_job("progression.events.tick")
    await progression.run_job("progression.events.tick")
    finished = await progression.outbox("progression.EventRunFinished")
    assert [e.payload["reason"] for e in finished] == ["ended"]
    assert len(await progression.outbox("progression.EventRunStarted")) == 1
    assert_problem(await progression.claim(user, KEY, "leaf"), 409, "progression.event_not_active")


async def test_stopped_run_is_not_finished_again_by_tick(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    run = await progression.publish_event(
        KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True))
    )
    await progression.client.post(
        f"/admin/v1/events/{KEY}/runs/{run}/stop",
        headers=progression.admin(ev.PUBLISH),
        json={"reason": "тест остановки"},
    )
    progression.clock.advance(days=30)
    await progression.run_job("progression.events.tick")
    assert len(await progression.outbox("progression.EventRunFinished")) == 1


async def test_item_deactivated_after_publish_is_not_granted_but_event_completes(
    progression: Service,
) -> None:
    ev = progression.ev
    await start_event(
        progression, ev.find("a", 1), ev.find("fin", 2, final=True), item="autumn_frame"
    )
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(text("UPDATE items SET active = false WHERE key = 'autumn_frame'"))
    user = uuid7()
    await progression.claim(user, KEY, "a")
    with capture_logs() as logs:
        final = (await progression.claim(user, KEY, "fin")).json()

    assert final["completed"] is True
    assert await progression.rows("inventory", user) == []
    (grant,) = await progression.rows("grants_log", user)
    assert grant.item_key == "autumn_frame"
    assert any(
        e["event"] == "progression.event_item_missing" and e["log_level"] == "error" for e in logs
    )
    (completed,) = await progression.outbox("progression.EventCompleted")
    assert completed.payload["item_key"] is None
    assert len(await progression.rows("event_completions", user)) == 1


async def test_item_moved_to_another_event_is_not_granted(progression: Service) -> None:
    ev = progression.ev
    await start_event(
        progression, ev.find("a", 1), ev.find("fin", 2, final=True), item="autumn_frame"
    )
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            text("UPDATE items SET event_key = 'winter' WHERE key = 'autumn_frame'")
        )
    user = uuid7()
    await progression.claim(user, KEY, "a")
    assert (await progression.claim(user, KEY, "fin")).json()["completed"] is True
    assert await progression.rows("inventory", user) == []


async def test_claim_after_stop_in_flight_gives_no_reward(
    progression: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    ev = progression.ev
    run = await start_event(progression, ev.find("leaf", 1), ev.find("fin", 2, final=True))
    original = Poster.lock_user

    async def stop_then_lock(self: Poster, user_id: UUID) -> Any:
        # остановка успевает закоммититься между чтением запуска и блокировкой игрока
        stopped = await progression.client.post(
            f"/admin/v1/events/{KEY}/runs/{run}/stop",
            headers=progression.admin(ev.PUBLISH),
            json={"reason": "тест остановки"},
        )
        assert stopped.status_code == 200, stopped.text
        return await original(self, user_id)

    monkeypatch.setattr(Poster, "lock_user", stop_then_lock)
    user = uuid7()
    response = await progression.claim(user, KEY, "leaf")
    assert_problem(response, 409, "progression.event_not_active")
    assert await progression.xp_rows(user) == 0
    assert await progression.rows("event_find_progress", user) == []


async def test_claim_of_not_yet_started_event_is_404(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config(at=progression.clock.now() - timedelta(days=60))
    await progression.publish_event(
        KEY,
        ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)),
        starts_at=progression.clock.now() + timedelta(days=1),
        ends_at=progression.clock.now() + timedelta(days=5),
    )
    user = uuid7()
    assert_problem(await progression.claim(user, KEY, "leaf"), 404, "progression.event_not_found")
    page_response = await progression.get(f"/v1/events/{KEY}", user)
    assert_problem(page_response, 404, "progression.event_not_found")


async def test_collection_tags_count_only_tagged_collections(progression: Service) -> None:
    """ADR 0067: находка с collection_tags засчитывает сессию по сборнику с таким тегом."""
    ev = progression.ev
    await start_event(
        progression,
        ev.find("coll", 1, ev.trigger("collection_played", collection_tags=["new_year"])),
        ev.find("fin", 2, final=True),
    )
    user = uuid7()
    cases = [
        ["rock"],  # другой тег
        [],  # у сборника нет тегов
        None,  # событие старого формата: поля нет вовсе
    ]
    for tags in cases:
        event = progression.game_finished(user, [10])
        event.payload["collection_id"] = str(uuid7())
        if tags is None:
            event.payload.pop("collection_tags", None)
        else:
            event.payload["collection_tags"] = tags
        await deliver(progression, event)
        assert finds_by_key(await page(progression, user))["coll"]["state"] == "open"
    tagged = progression.game_finished(user, [10])
    tagged.payload["collection_id"] = str(uuid7())
    tagged.payload["collection_tags"] = ["rock", "new_year"]
    await deliver(progression, tagged)
    assert finds_by_key(await page(progression, user))["coll"]["state"] == "found"
