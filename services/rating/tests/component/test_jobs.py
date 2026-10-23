"""Периодические задачи: сезоны, топ недели, лидерборды (G5) — с управляемыми часами."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rating_service.infrastructure.db.tables import seasons
from sqlalchemy import update

from gv_core.ids import uuid7
from gv_service import JobResult
from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.component

RatingService = Any  # фикстура `rating` описана в conftest.py
QUICK = {"calibration__matches": 1, "leaderboard__min_matches": 1}
# Начало недели 2026-10-26 по Москве (UTC+3, без перевода часов) — воскресенье 21:00 UTC.
MONDAY_MSK = datetime(2026, 10, 25, 21, 0, tzinfo=UTC)


# ------------------------------------------------------------------ сезоны
async def test_tick_does_nothing_while_season_runs(rating: RatingService) -> None:
    await rating.seed()
    started = len(await rating.outbox("rating.SeasonStarted"))
    assert await rating.run_job("rating.seasons.tick") is JobResult.OK
    assert len(await rating.outbox("rating.SeasonStarted")) == started
    assert await rating.outbox("rating.SeasonFinished") == []


async def test_tick_closes_season_by_schedule_and_starts_next_with_reset(
    rating: RatingService,
) -> None:
    first = await rating.seed(**QUICK)
    a, b = uuid7(), uuid7()
    await rating.play(a, b)
    async with rating.container.uow_factory()() as uow:
        season = await uow.seasons.active("film")
    assert season is not None
    rating.clock.advance(season.ends_at - rating.clock.now() + timedelta(seconds=1))

    await rating.run_job("rating.seasons.tick")
    finished = await rating.outbox("rating.SeasonFinished")
    started = await rating.outbox("rating.SeasonStarted")
    assert [e.payload["closed_by"] for e in finished] == ["schedule"]
    assert finished[0].payload["season_id"] == str(first)
    assert finished[0].payload["players_count"] == 2
    assert started[-1].payload["number"] == 2
    assert started[-1].payload["soft_reset"] == {"target": 1000, "ratio": 0.5}
    for envelope in (*finished, *started):
        assert_event_matches_schema(envelope)

    await rating.run_job("rating.seasons.tick")  # идемпотентно
    assert len(await rating.outbox("rating.SeasonFinished")) == 1
    assert len(await rating.outbox("rating.SeasonStarted")) == 2
    state = await rating.rating_of(a)
    assert state["season_id"] == started[-1].payload["season_id"]
    assert state["matches_played"] == 0
    assert state["rating"] == 1010  # 1020 после победы, мягкий сброс 50 % к 1000


async def test_season_length_comes_from_config(rating: RatingService) -> None:
    await rating.seed(season__length_days=3)
    async with rating.container.uow_factory()() as uow:
        season = await uow.seasons.active("film")
    assert season is not None
    assert season.ends_at - season.starts_at == timedelta(days=3)


async def test_tick_opens_season_when_ladder_has_none(rating: RatingService) -> None:
    season_id = await rating.seed()
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session, session.begin():
        await session.execute(
            update(seasons)
            .where(seasons.c.season_id == season_id)
            .values(
                status="finished",
                finished_at=rating.clock.now(),
                closed_by="admin",
                players_count=0,
            )
        )
    await rating.run_job("rating.seasons.tick")
    async with rating.container.uow_factory()() as uow:
        active = await uow.seasons.active("film")
    assert active is not None
    assert active.number == 2
    assert active.season_id != season_id


async def test_tick_activates_planned_season_when_due(rating: RatingService) -> None:
    await rating.seed(season__length_days=2)
    planned = await rating.client.post(
        "/admin/v1/ladders/film/seasons",
        json={"reason": "следующий"},
        headers={**rating.admin("rating.seasons.manage"), "Idempotency-Key": f"idem-{uuid7().hex}"},
    )
    assert planned.status_code == 201
    rating.clock.advance(days=2, seconds=1)
    await rating.run_job("rating.seasons.tick")
    async with rating.container.uow_factory()() as uow:
        active = await uow.seasons.active("film")
    assert active is not None
    assert str(active.season_id) == planned.json()["season_id"]
    assert len(await rating.outbox("rating.SeasonFinished")) == 1


async def test_finished_season_leaderboard_is_kept_without_ttl(rating: RatingService) -> None:
    season_id = await rating.seed(**QUICK)
    await rating.play(uuid7(), uuid7())
    async with rating.container.uow_factory()() as uow:
        season = await uow.seasons.active("film")
    assert season is not None
    rating.clock.advance(season.ends_at - rating.clock.now() + timedelta(seconds=1))
    redis = rating.infra.redis
    assert redis is not None
    ready = f"{{lb:film:{season_id}}}:ready"
    assert await redis.ttl(ready) > 0
    await rating.run_job("rating.seasons.tick")
    assert await redis.ttl(ready) == -1
    assert await redis.zcard(f"{{lb:film:{season_id}}}") == 2


# ------------------------------------------------------------------ топ недели
async def test_weekly_top_is_finalized_once_on_monday(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, u2, u3 = uuid7(), uuid7(), uuid7()
    await rating.play(u1, u2)
    await rating.play(u1, u3)
    await rating.play(u3, u2)
    await rating.run_job("rating.weekly.finalize")
    assert await rating.outbox("rating.WeeklyTopFinalized") == []  # неделя ещё идёт

    rating.clock.advance(MONDAY_MSK - rating.clock.now() + timedelta(minutes=1))
    assert await rating.run_job("rating.weekly.finalize") is JobResult.OK
    (event,) = await rating.outbox("rating.WeeklyTopFinalized")
    assert_event_matches_schema(event)
    assert event.aggregate_id == "film:2026-10-19"
    assert event.payload["week_start"] == "2026-10-19"
    top = event.payload["top"]
    assert [t["place"] for t in top] == list(range(1, len(top) + 1))
    assert top[0]["user_id"] == str(u1)
    assert all(t["delta"] > 0 for t in top)
    assert len(top) <= 10

    await rating.run_job("rating.weekly.finalize")
    assert len(await rating.outbox("rating.WeeklyTopFinalized")) == 1


async def test_weekly_top_size_comes_from_config(rating: RatingService) -> None:
    await rating.seed(weekly__top_size=1, **QUICK)
    u1, u2, u3 = uuid7(), uuid7(), uuid7()
    await rating.play(u1, u2)
    await rating.play(u3, u2)
    rating.clock.advance(MONDAY_MSK - rating.clock.now() + timedelta(minutes=1))
    await rating.run_job("rating.weekly.finalize")
    (event,) = await rating.outbox("rating.WeeklyTopFinalized")
    assert len(event.payload["top"]) == 1


async def test_week_boundary_follows_configured_timezone(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    a, b = uuid7(), uuid7()
    rating.clock.advance(MONDAY_MSK - rating.clock.now() - timedelta(minutes=1))
    await rating.play(a, b)  # 23:59 МСК воскресенья — ещё прошлая неделя
    rating.clock.advance(minutes=2)
    await rating.play(b, a)  # 00:01 МСК понедельника — новая неделя
    weekly = (await rating.client.get("/v1/ladders/film/weekly")).json()
    assert weekly["week_start"] == "2026-10-26"
    assert [i["user_id"] for i in weekly["items"]] == [str(b)]
    await rating.run_job("rating.weekly.finalize")
    (event,) = await rating.outbox("rating.WeeklyTopFinalized")
    assert event.payload["week_start"] == "2026-10-19"
    assert [t["user_id"] for t in event.payload["top"]] == [str(a)]


async def test_weekly_timezone_comes_from_config(rating: RatingService) -> None:
    await rating.seed(weekly__timezone="UTC", **QUICK)
    a, b = uuid7(), uuid7()
    rating.clock.advance(MONDAY_MSK - rating.clock.now() + timedelta(minutes=1))
    await rating.play(a, b)  # по UTC это ещё воскресенье
    await rating.run_job("rating.weekly.finalize")
    assert await rating.outbox("rating.WeeklyTopFinalized") == []
    weekly = (await rating.client.get("/v1/ladders/film/weekly")).json()
    assert weekly["week_start"] == "2026-10-19"


# ------------------------------------------------------------------ лидерборды
async def test_job_heals_lost_leaderboard_and_keeps_ttl_marker(rating: RatingService) -> None:
    season_id = await rating.seed(**QUICK)
    await rating.play(uuid7(), uuid7())
    redis = rating.infra.redis
    assert redis is not None
    key = f"{{lb:film:{season_id}}}"
    await redis.delete(key, f"{key}:ready")
    await rating.run_job("rating.leaderboards.rebuild")
    assert await redis.zcard(key) == 2
    ttl = await redis.ttl(f"{key}:ready")
    assert 0 < ttl <= rating.infra.settings.model_dump()["rating"]["leaderboard_resync_s"]


async def test_redis_outage_does_not_stop_rating(rating: RatingService) -> None:
    from rating_service.application.commands.leaderboards import Leaderboards

    from gv_core.errors import DependencyUnavailableError

    class Broken:
        async def put(self, *args: Any) -> None:
            raise DependencyUnavailableError("down")

        remove = put

    await rating.seed(**QUICK)
    boards = Leaderboards(rating.container.uow_factory(), Broken(), rating.clock)  # type: ignore[arg-type]  # заглушка с нужными методами
    rating.container.__dict__["boards"] = boards
    a, b = uuid7(), uuid7()
    await rating.play(a, b)
    assert len(await rating.outbox("rating.RatingChanged")) == 2
