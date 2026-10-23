"""Admin API rating: права, ladder-ы (черновик/публикация/откат), сезоны, пересборка (G6)."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from rating_service.infrastructure.db.tables import admin_actions, ratings
from sqlalchemy import select

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

RatingService = Any  # фикстура `rating` описана в conftest.py
REASON = {"reason": "проверка контракта"}
LADDERS = "rating.ladders.publish"
SEASONS = "rating.seasons.manage"
SID = str(uuid7())


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


async def call(
    rating: RatingService,
    method: str,
    path: str,
    body: Any = None,
    *,
    perm: str = LADDERS,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    sent = {**rating.admin(perm), **(headers if headers is not None else key())}
    return await rating.client.request(method, path, json=body, headers=sent)


def draft_body(rating_cfg: dict[str, Any]) -> dict[str, Any]:
    return {"config": rating_cfg, **REASON}


# ------------------------------------------------------------------ права
CASES: list[tuple[str, str, Any, str]] = [
    ("GET", "/admin/v1/ladders", None, LADDERS),
    ("GET", "/admin/v1/ladders/film/versions", None, LADDERS),
    ("GET", "/admin/v1/ladders/film/versions/1", None, LADDERS),
    ("PUT", "/admin/v1/ladders/film/draft", {"config": {}, **REASON}, LADDERS),
    ("POST", "/admin/v1/ladders/film/publish", {"version": 2, **REASON}, LADDERS),
    ("POST", "/admin/v1/ladders/film/rollback", {"to_version": 1, **REASON}, LADDERS),
    ("POST", "/admin/v1/ladders/film/seasons", REASON, SEASONS),
    ("POST", f"/admin/v1/seasons/{SID}/close", REASON, SEASONS),
    ("POST", "/admin/v1/ladders/film/leaderboard/rebuild", REASON, SEASONS),
]


@pytest.mark.parametrize("case", CASES, ids=[f"{m} {p}" for m, p, _, _ in CASES])
async def test_admin_needs_admin_access_and_specific_permission(
    rating: RatingService, case: tuple[str, str, Any, str]
) -> None:
    method, path, body, perm = case
    other = SEASONS if perm == LADDERS else LADDERS
    idem = key()
    anonymous = await rating.client.request(method, path, json=body, headers=idem)
    assert_problem(anonymous, 401, "auth.unauthenticated")
    tokens = [
        rating.issuer.issue_guest(),
        rating.issuer.issue(perms=["admin.access"]),
        rating.issuer.issue(perms=[perm]),
        rating.issuer.issue(perms=["admin.access", other]),
    ]
    for token in tokens:
        response = await rating.client.request(
            method, path, json=body, headers={**idem, "Authorization": f"Bearer {token}"}
        )
        assert_problem(response, 403, "auth.forbidden")
    allowed = await call(rating, method, path, body, perm=perm)
    assert allowed.status_code not in (401, 403), allowed.text


async def test_mutations_require_idempotency_key(rating: RatingService) -> None:
    await rating.seed()
    response = await call(
        rating, "POST", "/admin/v1/ladders/film/publish", {"version": 2, **REASON}, headers={}
    )
    assert_problem(response, 422, "idempotency.key_required")


# ------------------------------------------------------------------ ladder-ы
async def test_list_and_versions(rating: RatingService) -> None:
    season_id = await rating.seed()
    listing = (await call(rating, "GET", "/admin/v1/ladders")).json()
    assert listing["items"] == [
        {
            "key": "film",
            "published_version": 1,
            "draft_version": None,
            "season_id": str(season_id),
        }
    ]
    versions = (await call(rating, "GET", "/admin/v1/ladders/film/versions")).json()["items"]
    assert [(v["version"], v["status"]) for v in versions] == [(1, "published")]
    body = (await call(rating, "GET", "/admin/v1/ladders/film/versions/1")).json()
    assert body["config"]["elo"]["k"] == 20
    assert_problem(
        await call(rating, "GET", "/admin/v1/ladders/film/versions/9"),
        404,
        "rating.version_not_found",
    )
    assert_problem(
        await call(rating, "GET", "/admin/v1/ladders/music/versions"),
        404,
        "rating.ladder_not_found",
    )


async def test_draft_publish_changes_balance_for_new_matches(rating: RatingService) -> None:
    await rating.seed()
    config = rating.config("film", elo__k_calibrating=100)
    saved = await call(rating, "PUT", "/admin/v1/ladders/film/draft", draft_body(config))
    assert saved.status_code == 200, saved.text
    assert saved.json()["card"]["version"] == 2
    assert saved.json()["card"]["status"] == "draft"

    a, b = uuid7(), uuid7()
    await rating.play(a, b)  # до публикации действует версия 1 (K=40)
    first = [e.payload for e in await rating.outbox("rating.RatingChanged")]
    assert max(p["delta"] for p in first) == 20

    published = await call(
        rating, "POST", "/admin/v1/ladders/film/publish", {"version": 2, **REASON}
    )
    assert published.status_code == 200, published.text
    assert published.json()["card"]["status"] == "published"
    c, d = uuid7(), uuid7()
    await rating.play(c, d)
    deltas = {
        e.payload["user_id"]: e.payload["delta"]
        for e in await rating.outbox("rating.RatingChanged")
    }
    assert deltas[str(c)] == 50  # K=100 × 0,5

    announced = await rating.outbox("rating.LadderPublished")
    assert [e.payload["version"] for e in announced] == [1, 2]
    for envelope in announced:
        assert_event_matches_schema(envelope)
    listing = (await call(rating, "GET", "/admin/v1/ladders")).json()["items"][0]
    assert (listing["published_version"], listing["draft_version"]) == (2, None)


async def test_invalid_draft_is_rejected_with_paths(rating: RatingService) -> None:
    await rating.seed()
    broken = rating.config("film", elo__start="много")
    response = await call(rating, "PUT", "/admin/v1/ladders/film/draft", draft_body(broken))
    problem = assert_problem(response, 422, "rating.ladder_invalid")
    paths = {e["path"] for e in problem["details"]["errors"]}
    assert "/elo/start" in paths
    wrong_key = await call(
        rating, "PUT", "/admin/v1/ladders/film/draft", draft_body(rating.config("music"))
    )
    problem = assert_problem(wrong_key, 422, "rating.ladder_invalid")
    assert {e["path"] for e in problem["details"]["errors"]} == {"/key", "/domain"}
    missing = await call(rating, "PUT", "/admin/v1/ladders/nope/draft", draft_body(rating.config()))
    assert_problem(missing, 404, "rating.ladder_not_found")


async def test_publish_errors(rating: RatingService) -> None:
    await rating.seed()
    not_draft = await call(
        rating, "POST", "/admin/v1/ladders/film/publish", {"version": 1, **REASON}
    )
    assert_problem(not_draft, 409, "rating.version_not_draft")
    unknown = await call(rating, "POST", "/admin/v1/ladders/film/publish", {"version": 7, **REASON})
    assert_problem(unknown, 404, "rating.version_not_found")


async def test_rollback_publishes_copy_as_new_version(rating: RatingService) -> None:
    await rating.seed()
    await call(rating, "PUT", "/admin/v1/ladders/film/draft", draft_body(rating.config(elo__k=33)))
    await call(rating, "POST", "/admin/v1/ladders/film/publish", {"version": 2, **REASON})
    rolled = await call(
        rating, "POST", "/admin/v1/ladders/film/rollback", {"to_version": 1, **REASON}
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["card"]["version"] == 3
    assert rolled.json()["config"]["elo"]["k"] == 20
    versions = (await call(rating, "GET", "/admin/v1/ladders/film/versions")).json()["items"]
    assert [(v["version"], v["status"]) for v in versions] == [
        (3, "published"),
        (2, "archived"),
        (1, "archived"),
    ]
    assert [e.payload["version"] for e in await rating.outbox("rating.LadderPublished")] == [
        1,
        2,
        3,
    ]
    never = await call(
        rating, "PUT", "/admin/v1/ladders/film/draft", draft_body(rating.config(elo__k=44))
    )
    draft_version = never.json()["card"]["version"]
    assert_problem(
        await call(
            rating,
            "POST",
            "/admin/v1/ladders/film/rollback",
            {"to_version": draft_version, **REASON},
        ),
        409,
        "rating.version_not_published",
    )
    assert_problem(
        await call(rating, "POST", "/admin/v1/ladders/film/rollback", {"to_version": 99, **REASON}),
        404,
        "rating.version_not_found",
    )


async def test_mutation_is_idempotent_and_audited(rating: RatingService) -> None:
    await rating.seed()
    await call(rating, "PUT", "/admin/v1/ladders/film/draft", draft_body(rating.config(elo__k=33)))
    idem = key()
    body = {"version": 2, **REASON}
    first = await call(rating, "POST", "/admin/v1/ladders/film/publish", body, headers=idem)
    again = await call(rating, "POST", "/admin/v1/ladders/film/publish", body, headers=idem)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(await rating.outbox("rating.LadderPublished")) == 2
    reused = await call(
        rating, "POST", "/admin/v1/ladders/film/publish", {"version": 3, **REASON}, headers=idem
    )
    assert_problem(reused, 409, "idempotency.key_reused")
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        rows = (await session.execute(select(admin_actions.c.action, admin_actions.c.reason))).all()
    assert ("ladder.save_draft", REASON["reason"]) in [(r.action, r.reason) for r in rows]
    assert [r.action for r in rows].count("ladder.publish") == 1


# ------------------------------------------------------------------ сезоны
async def test_create_season_defaults_follow_current_and_overlap_is_rejected(
    rating: RatingService,
) -> None:
    season_id = await rating.seed()
    async with rating.container.uow_factory()() as uow:
        current = await uow.seasons.active("film")
    assert current is not None
    created = await call(rating, "POST", "/admin/v1/ladders/film/seasons", REASON, perm=SEASONS)
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["status"] == "scheduled"
    assert card["number"] == 2
    assert card["starts_at"] == current.ends_at.isoformat().replace("+00:00", "Z") or (
        card["starts_at"].startswith(current.ends_at.strftime("%Y-%m-%dT%H:%M:%S"))
    )
    overlap = await call(
        rating,
        "POST",
        "/admin/v1/ladders/film/seasons",
        {"starts_at": (current.starts_at + timedelta(days=1)).isoformat(), **REASON},
        perm=SEASONS,
    )
    assert_problem(overlap, 409, "rating.season_overlap")
    assert_problem(
        await call(rating, "POST", "/admin/v1/ladders/nope/seasons", REASON, perm=SEASONS),
        404,
        "rating.ladder_not_found",
    )
    assert season_id == current.season_id


async def test_close_season_resets_ratings_and_keeps_versions_monotonic(
    rating: RatingService,
) -> None:
    season_id = await rating.seed(calibration__matches=1, leaderboard__min_matches=1)
    a, b = uuid7(), uuid7()
    for _ in range(3):
        await rating.play(a, b)
    before = (await rating.rating_of(a))["rating"]
    assert before is not None and before > 1000

    closed = await call(
        rating, "POST", f"/admin/v1/seasons/{season_id}/close", REASON, perm=SEASONS
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "finished"

    finished = await rating.outbox("rating.SeasonFinished")
    started = await rating.outbox("rating.SeasonStarted")
    assert len(finished) == 1
    assert len(started) == 2
    for envelope in (*finished, *started):
        assert_event_matches_schema(envelope)
    assert finished[0].payload["closed_by"] == "admin"
    assert finished[0].payload["players_count"] == 2
    assert started[0].payload["soft_reset"] is None
    assert started[1].payload["soft_reset"] == {"target": 1000, "ratio": 0.5}
    new_season = UUID(started[1].payload["season_id"])
    assert new_season != season_id

    after = (await rating.rating_of(a))["rating"]
    assert after == 1000 + math.floor(0.5 * (before - 1000) + 0.5)  # округление от нуля
    await rating.play(a, b)
    last = [e.payload for e in await rating.outbox("rating.RatingChanged")][-2:]
    mine = next(p for p in last if p["user_id"] == str(a))
    assert mine["version"] == 4
    assert mine["season_id"] == str(new_season)
    assert mine["matches_played"] == 1
    assert mine["old"] == after
    assert mine["calibrating"] is False  # калибровка не обнуляется сбросом

    board = (
        await rating.client.get("/v1/ladders/film/leaderboard", params={"season": str(season_id)})
    ).json()
    assert board["season_id"] == str(season_id)
    assert len(board["items"]) == 2

    again = await call(rating, "POST", f"/admin/v1/seasons/{season_id}/close", REASON, perm=SEASONS)
    assert_problem(again, 409, "rating.season_not_active")
    assert_problem(
        await call(rating, "POST", f"/admin/v1/seasons/{uuid7()}/close", REASON, perm=SEASONS),
        404,
        "rating.season_not_found",
    )


async def test_close_activates_planned_season(rating: RatingService) -> None:
    season_id = await rating.seed()
    planned = (
        await call(rating, "POST", "/admin/v1/ladders/film/seasons", REASON, perm=SEASONS)
    ).json()
    await call(rating, "POST", f"/admin/v1/seasons/{season_id}/close", REASON, perm=SEASONS)
    async with rating.container.uow_factory()() as uow:
        active = await uow.seasons.active("film")
    assert active is not None
    assert str(active.season_id) == planned["season_id"]
    assert active.starts_at == rating.clock.now()


async def test_season_commands_are_idempotent(rating: RatingService) -> None:
    season_id = await rating.seed()
    idem = key()
    first = await call(
        rating, "POST", f"/admin/v1/seasons/{season_id}/close", REASON, perm=SEASONS, headers=idem
    )
    again = await call(
        rating, "POST", f"/admin/v1/seasons/{season_id}/close", REASON, perm=SEASONS, headers=idem
    )
    assert first.json() == again.json()
    assert len(await rating.outbox("rating.SeasonFinished")) == 1


# ------------------------------------------------------------------ пересборка
async def test_rebuild_is_queued_and_executed_by_job(rating: RatingService) -> None:
    season_id = await rating.seed(calibration__matches=1, leaderboard__min_matches=1)
    a, b = uuid7(), uuid7()
    await rating.play(a, b)
    redis = rating.infra.redis
    assert redis is not None
    await redis.delete(f"{{lb:film:{season_id}}}")  # запись есть, ZSET потерян, метка жива
    accepted = await call(
        rating, "POST", "/admin/v1/ladders/film/leaderboard/rebuild", REASON, perm=SEASONS
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json() == {"ladder": "film", "season_id": str(season_id), "status": "accepted"}
    assert await redis.zcard(f"{{lb:film:{season_id}}}") == 0
    await rating.run_job("rating.leaderboards.rebuild")
    assert await redis.zcard(f"{{lb:film:{season_id}}}") == 2
    await rating.run_job("rating.leaderboards.rebuild")  # повтор — без новых запросов
    assert_problem(
        await call(
            rating, "POST", "/admin/v1/ladders/nope/leaderboard/rebuild", REASON, perm=SEASONS
        ),
        404,
        "rating.ladder_not_found",
    )


async def test_ratings_table_is_not_touched_by_rebuild(rating: RatingService) -> None:
    await rating.seed(calibration__matches=1)
    a, b = uuid7(), uuid7()
    await rating.play(a, b)
    await call(rating, "POST", "/admin/v1/ladders/film/leaderboard/rebuild", REASON, perm=SEASONS)
    await rating.run_job("rating.leaderboards.rebuild")
    assert rating.infra.sessions is not None
    async with rating.infra.sessions() as session:
        total = (await session.execute(select(ratings.c.rating))).all()
    assert len(total) == 2
