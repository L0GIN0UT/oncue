"""Колесо и бусты (P2): спин раз в сутки, призы в ledger, параллельные спины, трата буста."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
TODAY = date(2026, 10, 23)


async def spin_ok(progression: Service, user: UUID) -> dict[str, Any]:
    response = await progression.spin(user)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body: dict[str, Any] = response.json()
    return body


# ---------------------------------------------------------------- просмотр
async def test_wheel_view_publishes_weights_and_chances(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    body = (await progression.get("/v1/wheel", user)).json()
    assert body["enabled"] is True
    assert len(body["sectors"]) == 12
    assert sum(s["chance"] for s in body["sectors"]) == pytest.approx(1)
    xp100 = next(s for s in body["sectors"] if s["key"] == "xp_100")
    assert (xp100["weight"], xp100["chance"]) == (18, pytest.approx(0.18))
    assert (body["day"], body["today"], body["next_spin_at"]) == (TODAY.isoformat(), None, None)

    progression.rig_wheel("xp_100")
    await spin_ok(progression, user)
    after = (await progression.get("/v1/wheel", user)).json()
    assert after["today"]["sector_key"] == "xp_100"
    assert after["today"]["repeated"] is True
    # обновление в полночь по Москве: 24 октября 00:00 MSK = 23 октября 21:00 UTC
    assert datetime.fromisoformat(after["next_spin_at"]) == datetime(
        2026, 10, 23, 21, 0, tzinfo=UTC
    )


# ---------------------------------------------------------------- призы
async def test_xp_prize_goes_to_ledger_with_wheel_source(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("xp_250")
    body = await spin_ok(progression, user)
    assert body["repeated"] is False
    assert body["prize"] == {
        "kind": "xp",
        "amount": 250,
        "multiplier": None,
        "matches": None,
        "item_key": None,
    }
    (row,) = await progression.rows("xp_ledger", user)
    assert (row.op_id, row.reason, row.amount) == (
        f"wheel:{user}:{TODAY.isoformat()}:xp",
        "wheel",
        250,
    )
    (event,) = await progression.outbox("progression.WheelSpun")
    assert event.payload["prize"] == {
        "kind": "xp",
        "amount": 250,
        "multiplier": None,
        "matches": None,
    }
    for e in await progression.outbox():
        assert_event_matches_schema(e)


async def test_verses_prize_posts_once_and_balance_matches(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("verses_50")
    await spin_ok(progression, user)
    again = await spin_ok(progression, user)
    assert again["repeated"] is True
    (row,) = await progression.rows("currency_ledger", user)
    assert row.op_id == f"wheel:{user}:{TODAY.isoformat()}"
    assert await progression.balance(user) == 50
    assert len(await progression.outbox("progression.WheelSpun")) == 1


async def test_item_prize_is_a_stub_entry_in_grants_log(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("item_cosmetic")
    body = await spin_ok(progression, user)
    assert body["prize"]["kind"] == "item" and body["prize"]["item_key"] == "wheel_cosmetic"
    (grant,) = await progression.rows("grants_log", user)
    assert (grant.item_key, grant.status, grant.source) == ("wheel_cosmetic", "pending", "wheel")
    assert await progression.rows("currency_ledger", user) == []
    (event,) = await progression.outbox("progression.WheelSpun")
    assert event.payload["prize"]["kind"] == "none"  # схема события не знает item
    assert_event_matches_schema(event)


async def test_nothing_prize_changes_no_balances(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("nothing")
    body = await spin_ok(progression, user)
    assert body["prize"]["kind"] == "none"
    assert await progression.rows("xp_ledger", user) == []
    assert await progression.rows("currency_ledger", user) == []
    assert await progression.rows("boosts", user) == []


# ---------------------------------------------------------------- идемпотентность
async def test_twenty_parallel_spins_give_one_record_one_posting_same_answer(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("verses_20")
    responses = await asyncio.gather(*(progression.spin(user) for _ in range(20)))
    assert {r.status_code for r in responses} == {200}
    bodies = [r.json() for r in responses]
    assert sum(not b["repeated"] for b in bodies) == 1
    assert len({(b["day"], b["sector_key"], b["spun_at"]) for b in bodies}) == 1
    assert len(await progression.rows("wheel_spins", user)) == 1
    assert len(await progression.rows("currency_ledger", user)) == 1
    assert await progression.balance(user) == 20
    assert len(await progression.outbox("progression.WheelSpun")) == 1


async def test_spin_again_next_local_day_and_day_follows_timezone(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("nothing")
    await spin_ok(progression, user)
    # 21:30 UTC = 00:30 следующих суток в Москве: колесо обновилось
    progression.clock.advance(timedelta(hours=9, minutes=30))
    second = await spin_ok(progression, user)
    assert (second["repeated"], second["day"]) == (False, "2026-10-24")
    assert len(await progression.rows("wheel_spins", user)) == 2


async def test_sector_is_chosen_by_server_and_body_is_rejected(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    for body in ({"sector_key": "verses_100"}, {"anything": 1}, [1]):
        assert_problem(
            await progression.spin(user, json=body), 422, "progression.spin_body_not_allowed"
        )
    assert_problem(
        await progression.spin(user, content=b"x"), 422, "progression.spin_body_not_allowed"
    )
    assert await progression.rows("wheel_spins", user) == []
    # пустое тело и пустой JSON-объект допустимы
    assert (await progression.spin(user)).status_code == 200


async def test_guest_cannot_spin_and_disabled_wheel_is_409(progression: Service) -> None:
    await progression.publish_config(wheel__enabled=False)
    guest = {"Authorization": f"Bearer {progression.issuer.issue_guest()}"}
    assert_problem(
        await progression.client.post("/v1/wheel/spin", headers=guest), 403, "auth.guest_forbidden"
    )
    user = uuid7()
    assert_problem(await progression.spin(user), 409, "progression.wheel_disabled")
    assert (await progression.get("/v1/wheel", user)).json()["enabled"] is False
    assert await progression.rows("wheel_spins", user) == []


async def test_spin_uses_weights_of_current_config_version(
    progression: Service, make_config: Any
) -> None:
    await progression.publish_config()
    sectors = [
        {**s, "weight": 1000 if s["key"] == "nothing" else 1}
        for s in make_config()["wheel"]["sectors"]
    ]
    await progression.publish_config(2, wheel__sectors=sectors)
    user = uuid7()
    progression.rig_wheel("nothing", config={"wheel": {"sectors": sectors}})
    body = await spin_ok(progression, user)
    assert body["sector_key"] == "nothing"
    (record,) = await progression.rows("wheel_spins", user)
    assert record.config_version == 2


# ---------------------------------------------------------------- бусты
async def test_boost_prize_applies_to_online_xp_only_and_expires(progression: Service) -> None:
    await progression.publish_config()
    user, rival = uuid7(), uuid7()
    progression.rig_wheel("boost_x2_3")
    body = await spin_ok(progression, user)
    assert body["prize"]["multiplier"] == 2 and body["prize"]["matches"] == 3
    me = (await progression.get("/v1/me/progress", user)).json()
    assert me["boost"] == {"multiplier": 2.0, "matches_left": 3} and me["boosts_queued"] == 0

    # дуэль: 40 + 20 + 60 победа = 120, плюс 200 первой победы дня - оба с множителем
    await progression.deliver(progression.duel(user, rival))
    matches = await progression.rows("xp_ledger", user, reason="duel")
    assert (matches[0].base_amount, matches[0].amount) == (120, 240)
    (first_win,) = await progression.rows("xp_ledger", user, reason="first_win")
    assert first_win.amount == 400
    # Версы не множатся
    (verse,) = await progression.rows("currency_ledger", user, reason="duel")
    assert verse.amount == 10
    # соло - без буста
    await progression.deliver(progression.game_finished(user, [100]))
    (solo,) = await progression.rows("xp_ledger", user, reason="solo_round")
    assert (solo.base_amount, solo.amount) == (30, 30)
    # у соперника буста нет
    (rival_row,) = await progression.rows("xp_ledger", rival)
    assert rival_row.amount == rival_row.base_amount

    assert (await progression.get("/v1/me/progress", user)).json()["boost"]["matches_left"] == 2
    for _ in range(2):
        await progression.deliver(progression.duel(user, uuid7()))
    assert (await progression.get("/v1/me/progress", user)).json()["boost"] is None
    await progression.deliver(progression.duel(user, uuid7()))
    last = await progression.rows("xp_ledger", user, reason="duel")
    assert last[-1].boost_multiplier == 1  # буст израсходован


async def test_boost_is_spent_once_per_match_even_if_event_is_replayed(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("boost_x2_3")
    await spin_ok(progression, user)
    match_id = uuid7()
    first = progression.duel(user, uuid7(), match_id=match_id)
    assert await progression.deliver(first) is Outcome.PROCESSED
    replay = progression.duel(user, uuid7(), match_id=match_id)  # новый event_id
    await progression.deliver(replay)
    (boost,) = await progression.rows("boosts", user)
    assert boost.matches_left == 2
    assert len(await progression.rows("boost_spends", user)) == 1
    assert len(await progression.rows("xp_ledger", user, reason="duel")) == 1


async def test_boost_is_not_spent_on_matches_without_xp(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("boost_x2_3")
    await spin_ok(progression, user)
    leaver = progression.match_finished(
        [
            {"user_id": user, "place": 2, "outcome": "loss", "finish": "forfeit"},
            {"user_id": uuid7(), "place": 1, "outcome": "win"},
        ],
        rounds=[[(user, 0)]],
    )
    await progression.deliver(leaver)
    (boost,) = await progression.rows("boosts", user)
    assert boost.matches_left == 3


async def test_second_boost_waits_in_queue_and_queue_is_limited(progression: Service) -> None:
    await progression.publish_config(wheel__max_boost_queue=1)
    user = uuid7()
    progression.rig_wheel("boost_x2_3")
    await spin_ok(progression, user)  # день 1: действующий
    progression.clock.advance(days=1)
    progression.rig_wheel("boost_x3_3")
    await spin_ok(progression, user)  # день 2: в очереди
    me = (await progression.get("/v1/me/progress", user)).json()
    assert me["boost"]["multiplier"] == 2.0 and me["boosts_queued"] == 1  # не складываются

    progression.clock.advance(days=1)
    progression.rig_wheel("boost_x2_5")
    full = await spin_ok(progression, user)  # день 3: очередь полна - приз пустой
    assert full["prize"]["kind"] == "none" and full["sector_key"] == "boost_x2_5"
    assert len(await progression.rows("boosts", user)) == 2
    # приз заменён на none везде: в ответе, в записи спина и в событии (сектор остался настоящим)
    spin3 = next(
        r for r in await progression.rows("wheel_spins", user) if r.sector_key == "boost_x2_5"
    )
    assert (spin3.prize_kind, spin3.prize_multiplier, spin3.prize_matches) == ("none", None, None)
    last_event = (await progression.outbox("progression.WheelSpun"))[-1]
    assert last_event.payload["sector_key"] == "boost_x2_5"
    assert last_event.payload["prize"] == {
        "kind": "none", "amount": 0, "multiplier": None, "matches": None,
    }  # fmt: skip
    assert_event_matches_schema(last_event)

    # израсходовать первый - действующим становится второй (x3)
    for _ in range(3):
        await progression.deliver(progression.duel(user, uuid7()))
    me = (await progression.get("/v1/me/progress", user)).json()
    assert me["boost"] == {"multiplier": 3.0, "matches_left": 3} and me["boosts_queued"] == 0


async def test_boost_hook_and_wheel_do_not_touch_rating(progression: Service) -> None:
    """Рейтинг считает сервис rating по MatchFinished; progression его события не публикует."""
    await progression.publish_config()
    user = uuid7()
    progression.rig_wheel("boost_x3_3")
    await spin_ok(progression, user)
    await progression.deliver(progression.duel(user, uuid7()))
    types = {e.event_type for e in await progression.outbox()}
    assert not any(t.startswith("progression.Rating") for t in types)
    assert "progression.ExperienceGained" in types


async def test_boost_is_not_spent_when_match_has_no_positive_base_xp(
    progression: Service,
) -> None:
    """Базового опыта нет (участие, раунды, победа и первая победа = 0) - множить нечего."""
    await progression.publish_config(
        xp__duel__participation=0,
        xp__duel__per_round_won=0,
        xp__duel__win_bonus=0,
        xp__first_win_of_day__bonus=0,
    )
    user = uuid7()
    progression.rig_wheel("boost_x2_3")
    await spin_ok(progression, user)
    await progression.deliver(progression.duel(user, uuid7()))
    (boost,) = await progression.rows("boosts", user)
    assert boost.matches_left == 3
    assert await progression.rows("boost_spends", user) == []
    assert await progression.rows("xp_ledger", user) == []
