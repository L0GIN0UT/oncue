"""Контракт шага 0a этапа 4 (§2.5, D5): internal-ручки game для match.

Только сервисный токен с ``aud = game`` и областью ``rounds.plan``; пользовательский JWT —
401, сервисный без области — 403, с областью — 501 до потока B.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

PLAN = {
    "mode_key": "film.lobby.collection",
    "seed": "match-0123456789",
    "rounds": 12,
    "collection_ids": [str(uuid7())],
    "exclude_ids": [str(uuid7())],
}
CASES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/internal/v1/round-plans", PLAN),
    ("GET", "/internal/v1/modes/film.duel.frame/versions/1", None),
]
IDS = [f"{m} {p}" for m, p, _ in CASES]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_internal_requires_service_token_with_rounds_plan_scope(
    game: Any, service_token: Callable[..., str], case: tuple[str, str, Any]
) -> None:
    method, path, body = case

    async def call(headers: dict[str, str]) -> Any:
        return await game.client.request(method, path, json=body, headers=headers)

    assert_problem(await call({}), 401, "auth.unauthenticated")
    # Пользовательский JWT (aud = guessverse) и сервисный токен другому сервису — 401.
    assert (await call(_bearer(game.issuer.issue(perms=["admin.access"])))).status_code == 401
    to_media = service_token("match", "media_pipeline", ["rounds.plan"])
    assert (await call(_bearer(to_media))).status_code == 401
    # Cookie на internal-ручках не принимается.
    game.client.cookies.set("gv_access", game.issuer.issue())
    try:
        assert (await call({})).status_code == 401
    finally:
        game.client.cookies.clear()

    no_scope = service_token("match", "game", ["assets.link"])
    assert_problem(await call(_bearer(no_scope)), 403, "auth.forbidden")

    allowed = service_token("match", "game", ["rounds.plan"])
    response = await call(_bearer(allowed))

    assert_problem(response, 501, "common.not_implemented")
    assert response.headers["cache-control"] == "no-store"


async def test_round_plan_request_is_validated(
    game: Any, service_token: Callable[..., str]
) -> None:
    headers = _bearer(service_token("match", "game", ["rounds.plan"]))
    for bad in (
        {**PLAN, "rounds": 0},
        {**PLAN, "rounds": 51},
        {**PLAN, "mode_key": "Film.duel"},
        {**PLAN, "seed": "short"},
        {**PLAN, "extra": True},
        {k: v for k, v in PLAN.items() if k != "seed"},
    ):
        response = await game.client.post("/internal/v1/round-plans", json=bad, headers=headers)
        assert response.status_code == 422, (bad, response.text)
    for path in (
        "/internal/v1/modes/film.duel.frame/versions/0",
        "/internal/v1/modes/film/versions/1",
    ):
        assert (await game.client.get(path, headers=headers)).status_code == 422
