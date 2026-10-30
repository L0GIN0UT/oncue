"""Награды за игровую сессию (``GET /v1/me/rewards``): только чтение журналов начислений."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.accrual.daily import daily_op_id, parse_daily_op_id

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
TODAY = date(2026, 10, 23)
PATH = "/v1/me/rewards"


async def rewards(progression: Service, user: UUID, session_id: UUID) -> dict[str, Any]:
    response = await progression.client.get(
        PATH, params={"session_id": str(session_id)}, headers=progression.user(user)
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body: dict[str, Any] = response.json()
    return body


async def test_solo_session_rewards_with_levels(progression: Service) -> None:
    await progression.publish_config()
    user, sid = uuid7(), uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100], session_id=sid))

    body = await rewards(progression, user, sid)

    assert body == {
        "session_id": str(sid),
        "settled": True,
        "xp": 90,
        "verses": 3,
        "level_before": 1,
        "level_after": 1,
        "streak": None,
    }


async def test_level_up_is_computed_against_earlier_xp(progression: Service) -> None:
    await progression.publish_config()
    user, first, second = uuid7(), uuid7(), uuid7()
    await progression.deliver(progression.game_finished(user, [100] * 3, session_id=first))
    progression.clock.advance(timedelta(minutes=1))
    await progression.deliver(progression.game_finished(user, [100] * 20, session_id=second))
    progression.clock.advance(timedelta(minutes=1))
    third = uuid7()
    await progression.deliver(progression.game_finished(user, [100] * 3, session_id=third))

    leveled = await rewards(progression, user, second)
    assert (leveled["xp"], leveled["level_before"], leveled["level_after"]) == (600, 1, 2)
    # Сессия до повышения не видит чужих по времени начислений и остаётся на первом уровне.
    early = await rewards(progression, user, first)
    assert (early["level_before"], early["level_after"]) == (1, 1)
    later = await rewards(progression, user, third)
    assert (later["xp"], later["level_before"], later["level_after"]) == (90, 2, 2)


async def test_not_yet_credited_session_is_empty_not_an_error(progression: Service) -> None:
    await progression.publish_config()
    user, sid = uuid7(), uuid7()

    pending = await rewards(progression, user, sid)
    assert pending["settled"] is False
    assert (pending["xp"], pending["verses"]) == (0, 0)
    assert pending["level_before"] is None
    assert pending["level_after"] is None

    await progression.deliver(progression.game_finished(user, [100, 100], session_id=sid))
    assert (await rewards(progression, user, sid))["settled"] is True


async def test_foreign_session_looks_like_not_credited(progression: Service) -> None:
    await progression.publish_config()
    owner, stranger, sid = uuid7(), uuid7(), uuid7()
    await progression.deliver(progression.game_finished(owner, [100, 100, 100], session_id=sid))

    seen_by_stranger = await rewards(progression, stranger, sid)
    unknown = await rewards(progression, stranger, uuid7())

    assert seen_by_stranger["settled"] is False
    assert (seen_by_stranger["xp"], seen_by_stranger["verses"]) == (0, 0)
    assert {**seen_by_stranger, "session_id": None} == {**unknown, "session_id": None}


async def test_daily_reward_carries_streak_until_it_moves_on(progression: Service) -> None:
    await progression.publish_config()
    user, day1, day2 = uuid7(), uuid7(), uuid7()
    # Раунды дейлика сами по себе ничего не начисляют: награда придёт с DailyCompleted.
    await progression.deliver(
        progression.game_finished(user, [100] * 5, day=TODAY, session_id=day1)
    )
    assert (await rewards(progression, user, day1))["settled"] is False

    await progression.deliver(progression.daily_completed(user, TODAY, session_id=day1))
    first = await rewards(progression, user, day1)
    assert (first["xp"], first["verses"]) == (60, 5)
    assert first["streak"] == {"domain": "music", "current": 1}

    await progression.deliver(
        progression.daily_completed(user, TODAY + timedelta(days=1), session_id=day2)
    )
    assert (await rewards(progression, user, day2))["streak"] == {"domain": "music", "current": 2}
    # Серия ушла на следующий день: вчерашней сессии «текущую серию» уже не приписываем.
    assert (await rewards(progression, user, day1))["streak"] is None


async def test_guest_anonymous_and_bad_query_are_rejected(progression: Service) -> None:
    sid = {"session_id": str(uuid7())}
    guest = {"Authorization": f"Bearer {progression.issuer.issue_guest()}"}
    assert_problem(
        await progression.client.get(PATH, params=sid, headers=guest), 403, "auth.guest_forbidden"
    )
    assert (await progression.client.get(PATH, params=sid)).status_code == 401
    bad = await progression.client.get(PATH, params={"session_id": "x"}, headers=progression.user())
    assert bad.status_code == 422
    missing = await progression.client.get(PATH, headers=progression.user())
    assert missing.status_code == 422


def test_daily_op_id_round_trip() -> None:
    user = uuid7()
    assert parse_daily_op_id(daily_op_id(user, "film", TODAY)) == ("film", TODAY)
    for foreign in (
        f"game.session:{user}:xp",
        "daily:x:y",
        f"daily:{user}::{TODAY}",
        "daily:a:b:c",
    ):
        assert parse_daily_op_id(foreign) is None
