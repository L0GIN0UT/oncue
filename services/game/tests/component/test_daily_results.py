"""Результаты дейлика игроков в текущем периоде (``GET /internal/v1/daily/results``, сервисный
токен bff_web) и признаки дейлика в итоге."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_problem

from .test_daily_flow import MUSIC_DAILY, bearer, play_out, prepared, round_item, start
from .test_daily_flow import move as move_

pytestmark = pytest.mark.component


async def finish_daily(game: Any, token: str, *, hit_first: bool) -> dict[str, Any]:
    """Начать дейлик и дойти до конца; ``hit_first`` — первый раунд угадан (6 очков)."""
    snap = (await start(game, token)).json()
    if hit_first:
        answer = await round_item(game, snap["id"], 0)
        r = await move_(
            game,
            token,
            snap["id"],
            "guesses",
            {
                "round_index": 0,
                "expected_version": snap["version"],
                "answer": {"media_item_id": str(answer)},
            },
        )
        snap = r.json()["session"]
    return await play_out(game, token, snap)


PATH = "/internal/v1/daily/results"


def service(game: Any, scopes: tuple[str, ...] = ("daily.results",), audience: str = "game") -> str:
    """Сервисный токен bff_web (по умолчанию — с областью ``daily.results`` для game)."""
    return str(game.issuer.issue_service("bff_web", audience, scopes))


async def results(
    game: Any, ids: list[UUID], token: str | None = None, **params: str
) -> httpx.Response:
    response: httpx.Response = await game.client.get(
        PATH,
        params=[("user_ids", str(i)) for i in ids] + list(params.items()),
        headers=bearer(token or service(game)),
    )
    return response


async def test_returns_only_finished_users_of_current_period(game: Any) -> None:
    await prepared(game)
    winner, loser, active, silent = uuid7(), uuid7(), uuid7(), uuid7()
    await finish_daily(game, game.issuer.issue(user_id=winner), hit_first=True)
    await finish_daily(game, game.issuer.issue(user_id=loser), hit_first=False)
    await start(game, game.issuer.issue(user_id=active))  # начал, но не закончил
    guest = game.issuer.issue_guest()
    await finish_daily(game, guest, hit_first=False)

    response = await results(game, [winner, loser, active, silent])

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    by_user = {UUID(r["user_id"]): r for r in response.json()}
    assert set(by_user) == {winner, loser}
    assert by_user[winner] == {
        "user_id": str(winner),
        "mode_key": MUSIC_DAILY,
        "domain": "music",
        "day": "2026-10-10",
        "score": 6,
        "max_score": 30,
    }
    assert by_user[loser]["score"] == 0


async def test_domain_filter_and_next_period_are_empty(game: Any) -> None:
    await prepared(game)
    player = uuid7()
    await finish_daily(game, game.issuer.issue(user_id=player), hit_first=True)

    assert len((await results(game, [player], domain="music")).json()) == 1
    assert (await results(game, [player], domain="film")).json() == []

    # Наступил следующий период: вчерашние очки чужих игроков не отдаются.
    game.clock.advance(timedelta(days=1))
    assert (await results(game, [player])).json() == []


async def test_only_service_token_with_daily_results_scope_is_accepted(game: Any) -> None:
    ids = [uuid7()]
    params = [("user_ids", str(i)) for i in ids]
    anonymous = await game.client.get(PATH, params=params)
    assert_problem(anonymous, 401, "auth.unauthenticated")
    # Пользовательский JWT (в том числе гостя) и токен другому сервису - 401.
    for token in (
        game.issuer.issue(),
        game.issuer.issue_guest(),
        service(game, audience="media_pipeline"),
    ):
        response = await game.client.get(PATH, params=params, headers=bearer(token))
        assert response.status_code == 401, response.text
    # Сервисный токен game без нужной области - 403.
    no_scope = await results(game, ids, service(game, scopes=("rounds.plan",)))
    assert_problem(no_scope, 403, "auth.forbidden")
    # Публичной ручки нет.
    public = await game.client.get(
        "/v1/daily/results", params=params, headers=bearer(game.issuer.issue())
    )
    assert public.status_code == 404


async def test_user_ids_are_capped(game: Any) -> None:
    over_setting = await results(game, [uuid7() for _ in range(51)])
    assert_problem(over_setting, 422, "game.too_many_users")
    assert over_setting.json()["details"] == {"given": 51, "max": 50}
    over_schema = await results(game, [uuid7() for _ in range(101)])
    assert over_schema.status_code == 422
    assert (await results(game, [])).status_code == 422


async def test_duplicate_ids_do_not_count_against_cap(game: Any) -> None:
    await prepared(game)
    player = uuid7()
    await finish_daily(game, game.issuer.issue(user_id=player), hit_first=True)
    response = await results(game, [player] * 60)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


async def test_result_carries_daily_markers(game: Any) -> None:
    await prepared(game)
    token = game.issuer.issue()
    final = await finish_daily(game, token, hit_first=False)
    out = (
        await game.client.get(f"/v1/sessions/{final['id']}/result", headers=bearer(token))
    ).json()
    assert out["daily"] is True
    assert out["mode_key"] == MUSIC_DAILY
    assert out["domain"] == "music"
    assert out["day"] == "2026-10-10"
