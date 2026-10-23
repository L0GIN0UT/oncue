"""Репозитории rating на реальном Postgres: партиции истории, блокировки, проекция профилей."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from gv_core.ids import uuid7
from gv_messaging import Outcome

pytestmark = pytest.mark.integration

RatingService = Any  # фикстура `rating` описана в conftest.py


async def test_history_lands_in_season_partition_not_default(rating: RatingService) -> None:
    season_id = await rating.seed()
    await rating.play(uuid7(), uuid7())
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        rows = (
            await session.execute(
                text("SELECT tableoid::regclass::text FROM rating_history GROUP BY 1")
            )
        ).all()
    assert [r[0] for r in rows] == [f"rating_history_s{season_id.hex}"]


async def test_new_season_gets_its_own_partition(rating: RatingService) -> None:
    await rating.seed()
    from datetime import timedelta

    created = await rating.client.post(
        "/admin/v1/ladders/film/seasons",
        json={"reason": "следующий"},
        headers={**rating.admin("rating.seasons.manage"), "Idempotency-Key": f"idem-{uuid7().hex}"},
    )
    assert created.status_code == 201
    new_id = created.json()["season_id"].replace("-", "")
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        found = (
            await session.execute(
                text("SELECT 1 FROM pg_tables WHERE tablename = :n"),
                {"n": f"rating_history_s{new_id}"},
            )
        ).first()
    assert found is not None
    del timedelta


async def test_concurrent_matches_of_one_player_get_distinct_versions(
    rating: RatingService,
) -> None:
    await rating.seed()
    a, b, c = uuid7(), uuid7(), uuid7()
    events = [rating.match_event(a, b), rating.match_event(c, a), rating.match_event(b, c)]
    outcomes = await asyncio.gather(*(rating.deliver(e) for e in events))
    assert outcomes == [Outcome.PROCESSED] * 3
    changed = [e.payload for e in await rating.outbox("rating.RatingChanged")]
    for user in (a, b, c):
        versions = sorted(p["version"] for p in changed if p["user_id"] == str(user))
        assert versions == [1, 2]


async def test_profile_projection_ignores_stale_versions_and_deleted(rating: RatingService) -> None:
    from gv_testkit import load_example

    user = uuid7()
    await rating.profile(user, "новый", version=3)
    await rating.profile(user, "старый", version=2)
    async with rating.container.uow_factory()() as uow:
        assert await uow.users.nicknames([user]) == {user: "новый"}
    deleted = load_example("identity.UserDeleted")
    await rating.deliver(
        deleted.model_copy(
            update={"payload": {**deleted.payload, "user_id": str(user), "version": 4}}
        )
    )
    await rating.profile(user, "вернулся", version=9)  # удалённый не оживает
    async with rating.container.uow_factory()() as uow:
        assert await uow.users.nicknames([user]) == {}
        assert not await uow.users.is_visible(user)
