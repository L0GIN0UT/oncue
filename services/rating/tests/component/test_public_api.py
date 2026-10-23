"""Публичный API rating: ladder-ы, лидерборды, топ недели, рейтинги, история (G4)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

RatingService = Any  # фикстура `rating` описана в conftest.py
# Калибровка в один матч и порог лидерборда в один матч: сценарии короче.
QUICK = {"calibration__matches": 1, "leaderboard__min_matches": 1}


async def three_players(rating: RatingService) -> tuple[UUID, UUID, UUID]:
    """u1 выиграл у u2 и у u3: итоговый порядок u1 > u3 > u2."""
    u1, u2, u3 = uuid7(), uuid7(), uuid7()
    for uid, name in ((u1, "alpha"), (u2, "beta"), (u3, "gamma")):
        await rating.profile(uid, name)
    await rating.play(u1, u2)
    await rating.play(u1, u3)
    return u1, u2, u3


# ------------------------------------------------------------------ ladder-ы
async def test_ladders_are_public_and_come_from_config(rating: RatingService) -> None:
    season_id = await rating.seed()
    response = await rating.client.get("/v1/ladders")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=10"
    (ladder,) = response.json()["items"]
    assert ladder["key"] == "film"
    assert ladder["version"] == 1
    assert ladder["formats"] == ["classic", "lite"]
    assert ladder["calibration_matches"] == 10
    assert ladder["season"]["season_id"] == str(season_id)
    keys = [t["key"] for t in ladder["tiers"]]
    assert keys[0] == "iron_1"
    assert ladder["tiers"][-1]["below"] is None
    with_token = await rating.client.get("/v1/ladders", headers=rating.user())
    assert with_token.headers["cache-control"] == "private, no-store"


# ------------------------------------------------------------------ лидерборд
async def test_leaderboard_orders_players_and_shows_nicknames(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, u2, u3 = await three_players(rating)
    response = await rating.client.get("/v1/ladders/film/leaderboard")
    assert response.status_code == 200
    page = response.json()
    assert [i["user_id"] for i in page["items"]] == [str(u1), str(u3), str(u2)]
    assert [i["place"] for i in page["items"]] == [1, 2, 3]
    assert [i["nickname"] for i in page["items"]] == ["alpha", "gamma", "beta"]
    assert page["items"][0]["tier"]["key"].startswith(("iron", "bronze"))
    assert page["next_cursor"] is None
    assert page["me"] is None


async def test_leaderboard_hides_players_in_calibration(rating: RatingService) -> None:
    await rating.seed()  # калибровка по конфигу — 10 матчей
    await three_players(rating)
    page = (await rating.client.get("/v1/ladders/film/leaderboard")).json()
    assert page["items"] == []


async def test_leaderboard_paginates_with_cursor(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, u2, u3 = await three_players(rating)
    first = (await rating.client.get("/v1/ladders/film/leaderboard", params={"limit": 2})).json()
    assert [i["user_id"] for i in first["items"]] == [str(u1), str(u3)]
    assert first["next_cursor"]
    second = (
        await rating.client.get(
            "/v1/ladders/film/leaderboard", params={"limit": 2, "cursor": first["next_cursor"]}
        )
    ).json()
    assert [(i["place"], i["user_id"]) for i in second["items"]] == [(3, str(u2))]
    assert second["next_cursor"] is None


async def test_leaderboard_default_page_size_comes_from_config(rating: RatingService) -> None:
    await rating.seed(leaderboard__page_size=2, **QUICK)
    await three_players(rating)
    page = (await rating.client.get("/v1/ladders/film/leaderboard")).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]


async def test_leaderboard_around_me_and_me_row(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    _, u2, u3 = await three_players(rating)
    around = await rating.client.get(
        "/v1/ladders/film/leaderboard",
        params={"around": "me", "limit": 2},
        headers=rating.user(u3),
    )
    assert around.status_code == 200
    body = around.json()
    assert body["me"]["user_id"] == str(u3)
    assert body["me"]["place"] == 2
    assert str(u3) in [i["user_id"] for i in body["items"]]
    assert around.headers["cache-control"] == "private, no-store"
    plain = (
        await rating.client.get("/v1/ladders/film/leaderboard", headers=rating.user(u2))
    ).json()
    assert plain["me"]["place"] == 3
    outsider = (
        await rating.client.get("/v1/ladders/film/leaderboard", headers=rating.user())
    ).json()
    assert outsider["me"] is None


async def test_leaderboard_around_me_errors(
    rating: RatingService,
) -> None:
    await rating.seed()  # калибровка 10: игрок вне лидерборда
    u1 = uuid7()
    await rating.play(u1, uuid7())
    params = {"around": "me"}
    anonymous = await rating.client.get("/v1/ladders/film/leaderboard", params=params)
    assert_problem(anonymous, 401, "auth.unauthenticated")
    guest = await rating.client.get(
        "/v1/ladders/film/leaderboard",
        params=params,
        headers={"Authorization": f"Bearer {rating.issuer.issue_guest()}"},
    )
    assert_problem(guest, 403, "auth.guest_forbidden")
    calibrating = await rating.client.get(
        "/v1/ladders/film/leaderboard", params=params, headers=rating.user(u1)
    )
    assert_problem(calibrating, 404, "rating.not_ranked")


async def test_leaderboard_not_found_cases(rating: RatingService) -> None:
    await rating.seed()
    assert_problem(
        await rating.client.get("/v1/ladders/music/leaderboard"), 404, "rating.ladder_not_found"
    )
    unknown_season = await rating.client.get(
        "/v1/ladders/film/leaderboard", params={"season": str(uuid7())}
    )
    assert_problem(unknown_season, 404, "rating.season_not_found")
    assert_problem(
        await rating.client.get("/v1/ladders/film/leaderboard", params={"cursor": "zzzz"}),
        422,
        "cursor.invalid",
    )


async def test_lost_redis_leaderboard_is_restored_by_job_not_by_read(
    rating: RatingService,
) -> None:
    season_id = await rating.seed(**QUICK)
    u1, _, _ = await three_players(rating)
    redis = rating.infra.redis
    assert redis is not None
    await redis.delete(f"{{lb:film:{season_id}}}", f"{{lb:film:{season_id}}}:ready")
    lost = await rating.client.get("/v1/ladders/film/leaderboard")
    assert lost.status_code == 503  # чтение не сканирует БД: ставит пересборку и просит повторить
    await rating.run_job("rating.leaderboards.rebuild")
    page = (await rating.client.get("/v1/ladders/film/leaderboard")).json()
    assert page["items"][0]["user_id"] == str(u1)
    assert len(page["items"]) == 3


# ------------------------------------------------------------------ топ недели
async def test_weekly_top_shows_positive_gains_only(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, u2, _ = await three_players(rating)
    anonymous = (await rating.client.get("/v1/ladders/film/weekly")).json()
    assert anonymous["week_start"] == "2026-10-19"
    assert [(i["user_id"], i["place"]) for i in anonymous["items"]] == [(str(u1), 1)]
    assert anonymous["items"][0]["delta"] > 0
    assert anonymous["items"][0]["nickname"] == "alpha"
    mine = (await rating.client.get("/v1/ladders/film/weekly", headers=rating.user(u1))).json()
    assert mine["me"]["place"] == 1
    other = (await rating.client.get("/v1/ladders/film/weekly", headers=rating.user(u2))).json()
    assert other["me"] is None
    assert_problem(
        await rating.client.get("/v1/ladders/music/weekly"), 404, "rating.ladder_not_found"
    )


async def test_weekly_top_hides_calibrating_players(rating: RatingService) -> None:
    await rating.seed()
    await three_players(rating)
    assert (await rating.client.get("/v1/ladders/film/weekly")).json()["items"] == []


# ------------------------------------------------------------------ рейтинги игрока
async def test_my_ratings_hide_numbers_in_calibration_then_show(rating: RatingService) -> None:
    await rating.seed(calibration__matches=2)
    a, b = uuid7(), uuid7()
    fresh = await rating.rating_of(a)
    assert (fresh["rating"], fresh["tier"], fresh["calibrating"]) == (None, None, True)
    assert fresh["calibration_left"] == 2
    await rating.play(a, b)
    after_one = await rating.rating_of(a)
    assert after_one["rating"] is None
    assert after_one["calibration_left"] == 1
    assert after_one["matches_played"] == 1
    await rating.play(a, b)
    ready = await rating.rating_of(a)
    assert ready["rating"] is not None
    assert ready["tier"]["key"]
    assert ready["next_tier_at"] is not None
    assert ready["calibration_left"] == 0
    assert ready["place"] is None  # порог лидерборда по умолчанию — 10 матчей


async def test_my_ratings_place_in_leaderboard(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, _, _ = await three_players(rating)
    assert (await rating.rating_of(u1))["place"] == 1


async def test_user_ratings_public_profile(rating: RatingService) -> None:
    await rating.seed(**QUICK)
    u1, _, _ = await three_players(rating)
    response = await rating.client.get(f"/v1/users/{u1}/ratings", headers=rating.user())
    assert response.status_code == 200
    assert response.json()["items"][0]["place"] == 1
    assert_problem(
        await rating.client.get(f"/v1/users/{uuid7()}/ratings", headers=rating.user()),
        404,
        "rating.user_not_found",
    )


async def test_deleted_user_disappears_from_boards_and_profile(rating: RatingService) -> None:
    from gv_testkit import load_example

    await rating.seed(**QUICK)
    u1, u2, u3 = await three_players(rating)
    base = load_example("identity.UserDeleted")
    event = base.model_copy(update={"payload": {**base.payload, "user_id": str(u1), "version": 5}})
    await rating.deliver(event)
    board = (await rating.client.get("/v1/ladders/film/leaderboard")).json()
    assert [i["user_id"] for i in board["items"]] == [str(u3), str(u2)]
    assert_problem(
        await rating.client.get(f"/v1/users/{u1}/ratings", headers=rating.user()),
        404,
        "rating.user_not_found",
    )
    weekly = (await rating.client.get("/v1/ladders/film/weekly")).json()
    assert weekly["items"] == []


async def test_personal_ratings_need_account(rating: RatingService) -> None:
    await rating.seed()
    guest = {"Authorization": f"Bearer {rating.issuer.issue_guest()}"}
    for path in (
        "/v1/me/ratings",
        f"/v1/users/{uuid7()}/ratings",
        "/v1/me/ratings/film/history",
        f"/v1/matches/{uuid7()}/changes",
    ):
        assert_problem(await rating.client.get(path), 401, "auth.unauthenticated")
        assert_problem(await rating.client.get(path, headers=guest), 403, "auth.guest_forbidden")
    mine = await rating.client.get("/v1/me/ratings", headers=rating.user())
    assert mine.headers["cache-control"] == "private, no-store"


# ------------------------------------------------------------------ история и итог
async def test_history_is_paginated_newest_first_and_hides_calibration(
    rating: RatingService,
) -> None:
    await rating.seed()
    a, b = uuid7(), uuid7()
    matches = []
    for _ in range(3):
        rating.clock.advance(seconds=60)
        matches.append((await rating.play(a, b)).payload["match_id"])
    headers = rating.user(a)
    first = (
        await rating.client.get("/v1/me/ratings/film/history", params={"limit": 2}, headers=headers)
    ).json()
    assert [i["match_id"] for i in first["items"]] == [matches[2], matches[1]]
    assert first["items"][0]["old"] is None
    assert first["items"][0]["delta"] is None
    assert first["items"][0]["outcome"] == "win"
    second = (
        await rating.client.get(
            "/v1/me/ratings/film/history",
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=headers,
        )
    ).json()
    assert [i["match_id"] for i in second["items"]] == [matches[0]]
    assert second["next_cursor"] is None
    assert_problem(
        await rating.client.get("/v1/me/ratings/music/history", headers=headers),
        404,
        "rating.ladder_not_found",
    )
    assert_problem(
        await rating.client.get(
            "/v1/me/ratings/film/history", params={"cursor": "!!"}, headers=headers
        ),
        422,
        "request.validation_failed",
    )


async def test_history_shows_numbers_after_calibration(rating: RatingService) -> None:
    await rating.seed(calibration__matches=1)
    a, b = uuid7(), uuid7()
    await rating.play(a, b)
    item = (await rating.client.get("/v1/me/ratings/film/history", headers=rating.user(a))).json()[
        "items"
    ][0]
    assert (item["old"], item["new"], item["delta"]) == (1000, 1020, 20)
    assert item["tier"]["key"]


async def test_match_changes_for_participants_only(rating: RatingService) -> None:
    await rating.seed()
    winner, loser = uuid7(), uuid7()
    event = await rating.play(winner, loser, loser_finish="forfeit")
    match_id = event.payload["match_id"]
    response = await rating.client.get(
        f"/v1/matches/{match_id}/changes", headers=rating.user(winner)
    )
    assert response.status_code == 200
    items = {i["user_id"]: i for i in response.json()["items"]}
    assert set(items) == {str(winner), str(loser)}
    assert items[str(loser)]["leaver_penalty"] == 10
    assert items[str(winner)]["calibrating"] is True
    assert items[str(winner)]["delta"] is None
    assert_problem(
        await rating.client.get(f"/v1/matches/{match_id}/changes", headers=rating.user()),
        404,
        "rating.match_not_rated",
    )
    assert_problem(
        await rating.client.get(f"/v1/matches/{uuid7()}/changes", headers=rating.user(winner)),
        404,
        "rating.match_not_rated",
    )
