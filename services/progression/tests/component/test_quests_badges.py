"""Задания и бейджи (P2): снимок на период, награды в ledger, идемпотентность, события."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
TODAY = date(2026, 10, 23)  # пятница; неделя с понедельника 2026-10-19


def quest(key: str, metric: str, target: int, **kw: Any) -> dict[str, Any]:
    return {
        "key": key,
        "period": kw.pop("period", "daily"),
        "title": key,
        "description": "",
        "metric": metric,
        "target": target,
        "reward": kw.pop("reward", {"xp": 30, "verses": 5}),
        "enabled": True,
        "domain": kw.pop("domain", None),
        "weight": 1,
        **kw,
    }


async def quest_rewards(progression: Service, user: UUID) -> int:
    """Проводки награды заданий (опыт) игрока."""
    return len(await progression.rows("xp_ledger", user, reason="quest"))


async def test_quests_are_assigned_as_snapshot_and_reward_goes_to_ledger(
    progression: Service,
) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    body = (await progression.get("/v1/me/quests", user)).json()
    assert sorted(q["key"] for q in body["daily"]["items"]) == [
        "daily_done",
        "guess_10",
        "play_3_solo",
    ]
    assert body["daily"]["period_start"] == TODAY.isoformat()
    assert body["weekly"]["period_start"] == "2026-10-19"
    assert body["weekly"]["period_end"] == "2026-10-25"
    assert sorted(q["key"] for q in body["one_time"]["items"]) == [
        "first_collection",
        "first_duel",
    ]
    assert body["one_time"]["period_end"] is None

    await progression.deliver(progression.game_finished(user, [10, 10, 10]))
    body = (await progression.get("/v1/me/quests", user)).json()
    done = next(q for q in body["daily"]["items"] if q["key"] == "play_3_solo")
    assert (done["progress"], done["completed"]) == (3, True)
    guess = next(q for q in body["daily"]["items"] if q["key"] == "guess_10")
    assert (guess["progress"], guess["completed"]) == (3, False)  # 3 угаданных из 10

    # награда: проводка опыта (источник quest) и Версы; события схем контракта
    (xp_row,) = await progression.rows("xp_ledger", user, reason="quest")
    assert xp_row.op_id == f"quest:{user}:play_3_solo:{TODAY.isoformat()}"
    assert xp_row.amount == 30
    assert len(await progression.rows("currency_ledger", user, reason="quest")) == 1
    (event,) = await progression.outbox("progression.QuestCompleted")
    assert event.payload["quest_key"] == "play_3_solo"
    assert event.payload["reward"] == {"xp": 30, "verses": 5}
    assert event.payload["period_start"] == TODAY.isoformat()
    for e in await progression.outbox():
        assert_event_matches_schema(e)


async def test_replayed_event_and_new_event_id_do_not_double_progress_or_reward(
    progression: Service,
) -> None:
    await progression.publish_config(rewards=True)
    user, sid = uuid7(), uuid7()
    first = progression.game_finished(user, [10, 10, 10], session_id=sid)
    assert await progression.deliver(first) is Outcome.PROCESSED
    assert await progression.deliver(first) is Outcome.DUPLICATE
    replay = progression.game_finished(user, [10, 10, 10], session_id=sid)  # новый event_id
    assert await progression.deliver(replay) is Outcome.PROCESSED

    assert await quest_rewards(progression, user) == 1
    assert len(await progression.rows("currency_ledger", user, reason="quest")) == 1
    (counter,) = await progression.rows("metric_counters", user, metric="rounds_played")
    assert counter.value == 3  # метрика не удвоена
    assert len(await progression.outbox("progression.QuestCompleted")) == 1


async def test_completed_quest_is_not_rewarded_again_by_more_progress(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    for _ in range(3):
        await progression.deliver(progression.game_finished(user, [10, 10, 10]))
    assert await quest_rewards(progression, user) == 1
    assert len(await progression.outbox("progression.QuestCompleted")) == 1


async def test_event_from_previous_period_counts_to_that_period(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    yesterday = progression.clock.now() - timedelta(days=1)
    await progression.deliver(progression.game_finished(user, [10, 10, 10], finished_at=yesterday))

    (xp_row,) = await progression.rows("xp_ledger", user, reason="quest")
    assert xp_row.op_id.endswith(f":play_3_solo:{(TODAY - timedelta(days=1)).isoformat()}")
    today_rows = await progression.rows(
        "quest_assignments", user, period="daily", period_start=TODAY
    )
    assert today_rows == []  # сегодняшний набор события не получил
    # недельный период — тот же (пятница и четверг одной недели)
    weekly = await progression.rows("quest_assignments", user, period="weekly")
    assert {r.period_start for r in weekly} == {date(2026, 10, 19)}


async def test_snapshot_survives_config_change_until_next_period(progression: Service) -> None:
    items = [quest("q_rounds", "rounds_played", 2, reward={"xp": 10, "verses": 1})]
    await progression.publish_config(
        rewards=True, quests__items=items, quests__daily_count=1, quests__weekly_count=0
    )
    user = uuid7()
    first = (await progression.get("/v1/me/quests", user)).json()["daily"]["items"][0]
    assert (first["target"], first["reward"]) == (2, {"xp": 10, "verses": 1})

    # админ меняет цель и награду (версия 2) - сегодняшний снимок не меняется (R8)
    changed = [quest("q_rounds", "rounds_played", 5, reward={"xp": 999, "verses": 9})]
    await progression.publish_config(
        2, rewards=True, quests__items=changed, quests__daily_count=1, quests__weekly_count=0
    )
    now = (await progression.get("/v1/me/quests", user)).json()["daily"]["items"][0]
    assert (now["target"], now["reward"]) == (2, {"xp": 10, "verses": 1})
    await progression.deliver(progression.game_finished(user, [10, 10]))
    (xp_row,) = await progression.rows("xp_ledger", user, reason="quest")
    assert xp_row.amount == 10

    progression.clock.advance(timedelta(days=1))  # следующий период - новая версия
    tomorrow = (await progression.get("/v1/me/quests", user)).json()["daily"]["items"][0]
    assert (tomorrow["target"], tomorrow["reward"]) == (5, {"xp": 999, "verses": 9})
    (snap,) = await progression.rows("quest_assignments", user, period_start=TODAY)
    assert snap.config_version == 1


async def test_domain_limited_quest_counts_only_its_domain(progression: Service) -> None:
    items = [quest("film_rounds", "rounds_played", 1, domain="film")]
    await progression.publish_config(
        rewards=True, quests__items=items, quests__daily_count=1, quests__weekly_count=0
    )
    user = uuid7()
    music = progression.game_finished(user, [10])
    music.payload["domain"] = "music"
    await progression.deliver(music)
    assert await quest_rewards(progression, user) == 0
    film = progression.game_finished(user, [10])
    film.payload["domain"] = "film"
    await progression.deliver(film)
    assert await quest_rewards(progression, user) == 1


async def test_assignment_is_deterministic_per_period(progression: Service) -> None:
    items = [quest(f"q{i}", "rounds_played", 1) for i in range(6)]
    await progression.publish_config(
        rewards=True, quests__items=items, quests__daily_count=2, quests__weekly_count=0
    )
    user = uuid7()
    first = [
        q["key"] for q in (await progression.get("/v1/me/quests", user)).json()["daily"]["items"]
    ]
    again = [
        q["key"] for q in (await progression.get("/v1/me/quests", user)).json()["daily"]["items"]
    ]
    assert len(first) == 2
    assert first == again
    # параллельные первые обращения не плодят лишние назначения
    other = uuid7()
    responses = await asyncio.gather(*(progression.get("/v1/me/quests", other) for _ in range(5)))
    assert {r.status_code for r in responses} == {200}
    sets = {tuple(sorted(q["key"] for q in r.json()["daily"]["items"])) for r in responses}
    assert len(sets) == 1
    assert len(await progression.rows("quest_assignments", other, period="daily")) == 2


async def test_one_time_quests_first_duel_and_first_collection(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    a, b = uuid7(), uuid7()
    await progression.deliver(progression.duel(a, b))
    await progression.deliver(progression.duel(a, b))
    assert {
        r.op_id.split(":")[2] for r in await progression.rows("xp_ledger", a, reason="quest")
    } == {"first_duel"}
    assert len(await progression.rows("xp_ledger", a, reason="quest")) == 1
    assert len(await progression.rows("xp_ledger", b, reason="quest")) == 1
    event = next(
        e
        for e in await progression.outbox("progression.QuestCompleted")
        if e.payload["user_id"] == str(a)
    )
    assert (event.payload["period"], event.payload["period_start"]) == (
        "one_time",
        TODAY.isoformat(),
    )

    cid = uuid7()
    await progression.deliver(progression.collection_published(a, collection_id=cid))
    await progression.deliver(
        progression.collection_published(a, collection_id=cid)
    )  # новая версия
    ops = [r.op_id for r in await progression.rows("xp_ledger", a, reason="quest")]
    assert sum(":first_collection:" in op for op in ops) == 1
    # кураторский и скрытый сборники не считаются
    await progression.deliver(progression.collection_published(None))
    await progression.deliver(progression.collection_published(b, status="hidden"))
    assert sum(":first_collection:" in r.op_id for r in await progression.rows("xp_ledger", b)) == 0


async def test_daily_completed_moves_daily_quest_and_streak_metric(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    await progression.deliver(progression.daily_completed(user, TODAY))
    ops = [r.op_id for r in await progression.rows("xp_ledger", user, reason="quest")]
    assert any(":daily_done:" in op for op in ops)
    (streak,) = await progression.rows("metric_counters", user, metric="daily_streak")
    assert streak.value == 1


async def test_guest_game_gives_nothing(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    await progression.deliver(progression.game_finished(None, [10, 10, 10]))
    assert await progression.rows("metric_counters") == []
    assert await progression.rows("quest_assignments") == []


# ---------------------------------------------------------------- бейджи
async def test_first_win_badge_once_and_public_profile(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    a, b = uuid7(), uuid7()
    await progression.deliver(progression.duel(a, b))
    await progression.deliver(progression.duel(a, b))
    unlocked = await progression.outbox("progression.BadgeUnlocked")
    assert [(e.payload["user_id"], e.payload["badge_key"]) for e in unlocked] == [
        (str(a), "first_win")
    ]
    assert_event_matches_schema(unlocked[0])

    mine = (await progression.get("/v1/me/badges", a)).json()["items"]
    first_win = next(x for x in mine if x["key"] == "first_win")
    assert first_win["unlocked_at"] is not None
    assert first_win["progress"] == first_win["target"] == 1
    other = next(
        x
        for x in (await progression.get("/v1/me/badges", b)).json()["items"]
        if x["key"] == "first_win"
    )
    assert other["unlocked_at"] is None and other["progress"] == 0

    profile = (await progression.get(f"/v1/users/{a}/progress", b)).json()
    assert [x["key"] for x in profile["badges"]] == ["first_win"]
    assert profile["badges"][0]["title"] == "Первая победа"
    assert (await progression.get(f"/v1/users/{b}/progress", a)).json()["badges"] == []


async def test_correct_answers_badge_accumulates(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    rounds = [
        {**r, "index": i}
        for i, r in enumerate(progression.game_finished(user, [1] * 60).payload["rounds"])
    ]
    await progression.deliver(progression.game_finished(user, [], rounds=rounds))
    mid = (await progression.get("/v1/me/badges", user)).json()["items"]
    g100 = next(x for x in mid if x["key"] == "guessed_100")
    assert (g100["progress"], g100["unlocked_at"]) == (60, None)
    await progression.deliver(progression.game_finished(user, [], rounds=rounds))
    done = next(
        x
        for x in (await progression.get("/v1/me/badges", user)).json()["items"]
        if x["key"] == "guessed_100"
    )
    assert done["unlocked_at"] is not None


async def test_streak_badge_from_seven_consecutive_days(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    for i in range(7):
        day = TODAY + timedelta(days=i)
        await progression.deliver(progression.daily_completed(user, day))
    keys = {e.payload["badge_key"] for e in await progression.outbox("progression.BadgeUnlocked")}
    assert "daily_streak_7" in keys
    assert "daily_streak_30" not in keys


async def test_season_tier_badge_from_standings(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    top, last, outside = uuid7(), uuid7(), uuid7()
    season = uuid7()
    await progression.deliver(
        progression.season_standings([(top, 1), (last, 1000), (outside, 5000)], season_id=season)
    )
    got = {
        e.payload["user_id"]: e.payload["badge_key"]
        for e in await progression.outbox("progression.BadgeUnlocked")
    }
    assert got == {str(top): "season_tier", str(last): "season_tier"}
    # звание лучшей полосы выше: 6 полос -> 6, последняя -> 1
    tiers = {
        r.user_id: r.value for r in await progression.rows("metric_counters", metric="season_tier")
    }
    assert tiers == {top: 6, last: 1}
