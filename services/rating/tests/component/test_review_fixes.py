"""Правки по ревью потока G: гонки Redis, недели, сезоны, границы, безопасность (S1–S10)."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from rating_service.application.commands.leaderboards import Leaderboards
from rating_service.infrastructure.db.tables import (
    admin_actions,
    rebuild_requests,
    seasons,
    weekly_deltas,
)
from rating_service.infrastructure.leaderboard import RedisLeaderboard, board_key
from sqlalchemy import select, update

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_problem, load_example

pytestmark = pytest.mark.component

RatingService = Any  # фикстура `rating` описана в conftest.py
QUICK = {"calibration__matches": 1, "leaderboard__min_matches": 1}
MONDAY_MSK_UTC_OFFSET = timedelta(hours=3)
NIL = "00000000-0000-0000-0000-000000000000"


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


def monday_after(rating: RatingService) -> timedelta:
    """Сдвиг часов до 00:01 МСК ближайшего понедельника (2026-10-26)."""
    from datetime import UTC, datetime

    target = datetime(2026, 10, 25, 21, 1, tzinfo=UTC)
    return target - rating.clock.now()


# ------------------------------------------------------------------ (1) гонка Redis
async def test_stale_leaderboard_write_does_not_regress_newer_rating(
    rating: RatingService,
) -> None:
    season_id = await rating.seed(**QUICK)
    u1, u2 = uuid7(), uuid7()
    await rating.play(u1, u2)
    await rating.play(u1, u2)
    async with rating.container.uow_factory()() as uow:
        newest = (await uow.ratings.many("film", season_id, [u1]))[u1]
        config = (await uow.ladders.published("film")).config  # type: ignore[union-attr]
    stale = type(newest)(
        user_id=u1,
        ladder="film",
        season_id=season_id,
        rating=newest.rating - 50,
        matches_total=newest.matches_total,
        season_matches=newest.season_matches,
        event_version=newest.event_version - 1,
        updated_at=newest.updated_at,
    )
    await rating.container.boards.sync(config, stale)
    redis = rating.infra.redis
    assert redis is not None
    assert int(await redis.zscore(board_key("film", season_id), str(u1))) == newest.rating


async def test_rebuild_repairs_write_that_raced_with_snapshot(rating: RatingService) -> None:
    season_id = await rating.seed(**QUICK)
    u1, u2 = uuid7(), uuid7()
    await rating.play(u1, u2)

    class RacyStore(RedisLeaderboard):
        hook: Any = None

        async def replace(self, *args: Any, **kwargs: Any) -> None:
            if self.hook is not None:
                hook, self.hook = self.hook, None
                await hook()  # матч закоммитился между чтением БД и записью в Redis
            await super().replace(*args, **kwargs)

    racy = RacyStore(rating.infra.redis, ready_ttl_s=60)
    racy.hook = lambda: rating.play(u1, u2)
    boards = Leaderboards(rating.container.uow_factory(), racy, rating.clock)
    await boards.rebuild("film", season_id)
    async with rating.container.uow_factory()() as uow:
        current = await uow.ratings.many("film", season_id, [u1, u2])
    assert current[u1].event_version == 2
    redis = rating.infra.redis
    assert redis is not None
    for uid in (u1, u2):
        assert (
            int(await redis.zscore(board_key("film", season_id), str(uid))) == current[uid].rating
        )


# ------------------------------------------------------------------ (2) поздний матч
async def test_match_after_week_closed_counts_in_current_week(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, u2, u3 = uuid7(), uuid7(), uuid7()
    friday = rating.clock.now().isoformat()
    await rating.play(u1, u2)
    rating.clock.advance(monday_after(rating))
    await rating.run_job("rating.weekly.finalize")
    (closed,) = await rating.outbox("rating.WeeklyTopFinalized")
    assert closed.payload["week_start"] == "2026-10-19"

    late = rating.match_event(u3, u2, finished_at=friday)  # финал пятницы дошёл после закрытия
    assert await rating.deliver(late) is Outcome.PROCESSED
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        rows = (
            await session.execute(
                select(weekly_deltas.c.week_start, weekly_deltas.c.user_id, weekly_deltas.c.delta)
            )
        ).all()
    by_week = {(str(r.week_start), r.user_id): r.delta for r in rows}
    assert (("2026-10-19", u3)) not in by_week  # закрытая неделя не меняется
    assert by_week[("2026-10-26", u3)] > 0
    await rating.run_job("rating.weekly.finalize")
    assert len(await rating.outbox("rating.WeeklyTopFinalized")) == 1


# ------------------------------------------------------------------ (3) сезон недели
async def test_weekly_event_season_is_the_season_of_the_last_match(rating: RatingService) -> None:
    first = await rating.seed(**QUICK)
    u1, u2, u3, u4 = uuid7(), uuid7(), uuid7(), uuid7()
    await rating.play(u1, u2)
    closed = await rating.client.post(
        f"/admin/v1/seasons/{first}/close",
        json={"reason": "тест"},
        headers={**rating.admin("rating.seasons.manage"), **idem()},
    )
    assert closed.status_code == 200
    rating.clock.advance(minutes=5)
    await rating.play(u3, u4)
    async with rating.container.uow_factory()() as uow:
        second = await uow.seasons.active("film")
    assert second is not None
    rating.clock.advance(monday_after(rating))
    await rating.run_job("rating.weekly.finalize")
    (event,) = await rating.outbox("rating.WeeklyTopFinalized")
    assert event.payload["season_id"] == str(second.season_id)
    assert {t["user_id"] for t in event.payload["top"]} == {str(u1), str(u3)}


# ------------------------------------------------------------------ (4) ранний старт
async def test_early_started_season_gets_full_length(rating: RatingService) -> None:
    first = await rating.seed()
    planned = await rating.client.post(
        "/admin/v1/ladders/film/seasons",
        json={"reason": "следующий"},
        headers={**rating.admin("rating.seasons.manage"), **idem()},
    )
    assert planned.status_code == 201
    rating.clock.advance(days=3)
    await rating.client.post(
        f"/admin/v1/seasons/{first}/close",
        json={"reason": "досрочно"},
        headers={**rating.admin("rating.seasons.manage"), **idem()},
    )
    async with rating.container.uow_factory()() as uow:
        active = await uow.seasons.active("film")
    assert active is not None
    assert str(active.season_id) == planned.json()["season_id"]
    assert active.starts_at == rating.clock.now()
    assert active.ends_at == rating.clock.now() + timedelta(days=56)
    started = (await rating.outbox("rating.SeasonStarted"))[-1]
    assert started.payload["ends_at"].startswith(active.ends_at.strftime("%Y-%m-%dT%H:%M:%S"))


# ------------------------------------------------------------------ (5) параллельные матчи
async def test_many_concurrent_matches_do_not_deadlock_or_lose_updates(
    rating: RatingService,
) -> None:
    await rating.seed()
    players = [uuid7() for _ in range(4)]
    await rating.play(players[0], players[1])  # часть игроков уже с рейтингом
    pairs = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2), (1, 3), (2, 0), (3, 1)]
    events = [rating.match_event(players[a], players[b]) for a, b in pairs]
    async with asyncio.timeout(60):
        outcomes = await asyncio.gather(*(rating.deliver(e) for e in events))
    assert outcomes == [Outcome.PROCESSED] * len(events)
    changed = [e.payload for e in await rating.outbox("rating.RatingChanged")]
    for uid in players:
        versions = sorted(p["version"] for p in changed if p["user_id"] == str(uid))
        assert versions == list(range(1, len(versions) + 1))
    total = sum(p["delta"] for p in changed)
    assert total == 0  # K калибровки одинаков у всех, штрафов нет — игра с нулевой суммой


# ------------------------------------------------------------------ (6)/(S1) конфиг
async def test_custom_queue_format_is_rated(rating: RatingService) -> None:
    raw = rating.config(
        formats=["classic", "blitz"], elo__format_multiplier={"classic": 1, "blitz": 0.5}
    )
    await rating.container.ladder_commands.bootstrap(raw, actor_id=UUID(int=1), reason="сид")
    u1, u2 = uuid7(), uuid7()
    await rating.play(u1, u2, queue_format="blitz")
    changed = [e.payload for e in await rating.outbox("rating.RatingChanged")]
    assert {p["format"] for p in changed} == {"blitz"}
    from gv_testkit import assert_event_matches_schema

    for envelope in await rating.outbox():
        assert_event_matches_schema(envelope)  # схемы событий не ограничивают набор форматов
    event = rating.match_event(uuid7(), uuid7(), queue_format="lite")
    assert await rating.deliver(event) is Outcome.DEAD  # формата нет в этом ladder-е


async def test_broken_stored_config_goes_to_dlq_not_infinite_retries(
    rating: RatingService,
) -> None:
    await rating.seed()
    from rating_service.infrastructure.db.tables import ladder_versions

    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session, session.begin():
        await session.execute(update(ladder_versions).values(config={"key": "film", "surprise": 1}))
    event = rating.match_event(uuid7(), uuid7())
    assert await rating.deliver(event) is Outcome.DEAD


async def test_draft_with_out_of_bounds_values_is_rejected(rating: RatingService) -> None:
    await rating.seed()
    headers = {**rating.admin("rating.ladders.publish"), **idem()}
    bad = rating.config(elo__k=10**9, season__length_days=1000)
    response = await rating.client.put(
        "/admin/v1/ladders/film/draft", json={"config": bad, "reason": "плохо"}, headers=headers
    )
    problem = assert_problem(response, 422, "rating.ladder_invalid")
    assert {"/elo/k", "/season/length_days"} <= {e["path"] for e in problem["details"]["errors"]}


async def test_draft_with_nan_is_rejected(rating: RatingService) -> None:
    await rating.seed()
    body = json.dumps({"config": rating.config(), "reason": "nan"}).replace('"k": 20', '"k": NaN')
    assert "NaN" in body
    response = await rating.client.put(
        "/admin/v1/ladders/film/draft",
        content=body,
        headers={
            **rating.admin("rating.ladders.publish"),
            **idem(),
            "content-type": "application/json",
        },
    )
    assert response.status_code == 422, response.text


# ------------------------------------------------------------------ (S2) удалённый игрок
async def test_deleted_user_never_returns_to_leaderboard_via_new_matches(
    rating: RatingService,
) -> None:
    await rating.seed(**QUICK)
    u1, u2 = uuid7(), uuid7()
    await rating.profile(u1, "ghost")
    await rating.profile(u2, "alive")
    await rating.play(u1, u2)
    base = load_example("identity.UserDeleted")
    await rating.deliver(
        base.model_copy(update={"payload": {**base.payload, "user_id": str(u1), "version": 9}})
    )
    await rating.play(u1, u2)
    board = (await rating.client.get("/v1/ladders/film/leaderboard")).json()
    assert [i["user_id"] for i in board["items"]] == [str(u2)]
    assert len(await rating.outbox("rating.RatingChanged")) == 4  # матч посчитан


# ------------------------------------------------------------------ (S3) finished_at
@pytest.mark.parametrize(
    "finished_at",
    ["2030-01-01T00:00:00+00:00", "2026-10-23T12:00:00", "not-a-date"],
    ids=["future", "naive", "garbage"],
)
async def test_invalid_finished_at_goes_to_dlq(rating: RatingService, finished_at: str) -> None:
    await rating.seed()
    event = rating.match_event(uuid7(), uuid7(), finished_at=finished_at)
    assert await rating.deliver(event) is Outcome.DEAD
    assert await rating.outbox("rating.RatingChanged") == []


async def test_small_clock_skew_is_tolerated(rating: RatingService) -> None:
    await rating.seed()
    near = (rating.clock.now() + timedelta(seconds=30)).isoformat()
    assert (
        await rating.deliver(rating.match_event(uuid7(), uuid7(), finished_at=near))
        is Outcome.PROCESSED
    )


# ------------------------------------------------------------------ (S4) сезоны админа
@pytest.mark.parametrize(
    ("starts_at", "status", "code"),
    [
        ("2026-12-01T00:00:00", 422, "request.validation_failed"),  # без часового пояса
        ("2031-01-01T00:00:00+00:00", 422, "rating.season_start_invalid"),
        ("2020-01-01T00:00:00+00:00", 422, "rating.season_start_invalid"),
    ],
    ids=["naive", "far_future", "far_past"],
)
async def test_season_start_window(
    rating: RatingService, starts_at: str, status: int, code: str
) -> None:
    await rating.seed()
    response = await rating.client.post(
        "/admin/v1/ladders/film/seasons",
        json={"starts_at": starts_at, "reason": "окно"},
        headers={**rating.admin("rating.seasons.manage"), **idem()},
    )
    assert_problem(response, status, code)


async def test_new_season_cannot_overlap_finished_season(rating: RatingService) -> None:
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
    start = (rating.clock.now() - timedelta(seconds=60)).isoformat()
    response = await rating.client.post(
        "/admin/v1/ladders/film/seasons",
        json={"starts_at": start, "reason": "наложение"},
        headers={**rating.admin("rating.seasons.manage"), **idem()},
    )
    assert_problem(response, 409, "rating.season_overlap")


# ------------------------------------------------------------------ (S5) промах по ключу
async def test_public_read_of_lost_board_does_not_scan_and_queues_one_rebuild(
    rating: RatingService,
) -> None:
    season_id = await rating.seed(**QUICK)
    u1, u2 = uuid7(), uuid7()
    await rating.play(u1, u2)
    redis = rating.infra.redis
    assert redis is not None
    key = board_key("film", season_id)
    await redis.delete(key, f"{key}:ready")
    for _ in range(3):
        response = await rating.client.get("/v1/ladders/film/leaderboard")
        assert response.status_code == 503
        assert response.json()["code"] == "dependency.unavailable"
    assert await redis.zcard(key) == 0  # синхронной пересборки нет
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        queued = (await session.execute(select(rebuild_requests.c.id))).all()
    assert len(queued) == 1
    await rating.run_job("rating.leaderboards.rebuild")
    page = await rating.client.get("/v1/ladders/film/leaderboard")
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2


# ------------------------------------------------------------------ (S6) маскировка
async def test_deleted_user_id_is_masked_in_changes_and_leaderboard(rating: RatingService) -> None:
    season_id = await rating.seed(**QUICK)
    u1, u2 = uuid7(), uuid7()
    await rating.profile(u1, "ghost")
    await rating.profile(u2, "alive")
    event = await rating.play(u1, u2)
    base = load_example("identity.UserDeleted")
    await rating.deliver(
        base.model_copy(update={"payload": {**base.payload, "user_id": str(u1), "version": 9}})
    )
    changes = await rating.client.get(
        f"/v1/matches/{event.payload['match_id']}/changes", headers=rating.user(u2)
    )
    ids = {i["user_id"] for i in changes.json()["items"]}
    assert ids == {NIL, str(u2)}
    redis = rating.infra.redis
    assert redis is not None
    await redis.zadd(board_key("film", season_id), {str(u1): 5000})  # отставшая запись Redis
    board = (await rating.client.get("/v1/ladders/film/leaderboard")).json()
    assert board["items"][0]["user_id"] == NIL
    assert board["items"][0]["nickname"] == ""


# ------------------------------------------------------------------ (S8) курсор
async def test_cursor_offset_is_bounded(rating: RatingService) -> None:
    from rating_service.application.common import encode_cursor

    await rating.seed()
    huge = encode_cursor({"o": 10**12})
    response = await rating.client.get("/v1/ladders/film/leaderboard", params={"cursor": huge})
    assert_problem(response, 422, "cursor.invalid")
    negative = encode_cursor({"o": -1})
    response = await rating.client.get("/v1/ladders/film/leaderboard", params={"cursor": negative})
    assert_problem(response, 422, "cursor.invalid")


# ------------------------------------------------------------------ (S9) bootstrap
async def test_bootstrap_is_audited(rating: RatingService) -> None:
    await rating.seed()
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        rows = (await session.execute(select(admin_actions.c.action, admin_actions.c.target))).all()
    assert ("ladder.bootstrap", "film") in [(r.action, r.target) for r in rows]


# ------------------------------------------------------------------ (S10) ядовитый запрос
async def test_poison_rebuild_request_does_not_block_others(rating: RatingService) -> None:
    season_id = await rating.seed(**QUICK)
    u1, u2 = uuid7(), uuid7()
    await rating.play(u1, u2)
    redis = rating.infra.redis
    assert redis is not None
    await redis.delete(board_key("film", season_id), f"{board_key('film', season_id)}:ready")
    async with rating.container.uow_factory()() as uow:
        await uow.rebuilds.request("film", uuid7(), "ядовитый", UUID(int=1), rating.clock.now())
        await uow.rebuilds.request("film", season_id, "нормальный", UUID(int=1), rating.clock.now())
        await uow.commit()
    await rating.run_job("rating.leaderboards.rebuild")
    assert await redis.zcard(board_key("film", season_id)) == 2
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        rows = (
            await session.execute(select(rebuild_requests.c.done_at, rebuild_requests.c.error))
        ).all()
    assert all(r.done_at is not None for r in rows)
    assert sorted(bool(r.error) for r in rows) == [False, True]


# ------------------------------------------------------------------ (9) weekly_tops.season_id
async def test_empty_week_is_closed_with_null_season(rating: RatingService) -> None:
    from rating_service.infrastructure.db.tables import weekly_tops

    await rating.seed()  # калибровка 10: приростов в топ нет
    await rating.play(uuid7(), uuid7())
    rating.clock.advance(monday_after(rating))
    await rating.run_job("rating.weekly.finalize")
    assert await rating.outbox("rating.WeeklyTopFinalized") == []
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        rows = (await session.execute(select(weekly_tops.c.season_id, weekly_tops.c.top))).all()
    assert [(r.season_id, r.top) for r in rows] == [(None, [])]
