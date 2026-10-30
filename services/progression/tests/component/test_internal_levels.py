"""Уровни игроков пачкой: ``GET /internal/v1/levels`` под сервисным токеном bff_web."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.infrastructure.db.tables import progress
from sqlalchemy import update

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
PATH = "/internal/v1/levels"


def service(
    svc: Service, scopes: tuple[str, ...] = ("levels.read",), audience: str = "progression"
) -> str:
    """Сервисный токен bff_web (по умолчанию — с областью ``levels.read`` для progression)."""
    return str(svc.issuer.issue_service("bff_web", audience, scopes))


async def levels(svc: Service, ids: list[UUID], token: str | None = None) -> httpx.Response:
    response: httpx.Response = await svc.client.get(
        PATH,
        params=[("user_ids", str(i)) for i in ids],
        headers={"Authorization": f"Bearer {token or service(svc)}"},
    )
    return response


async def test_returns_level_and_xp_in_level_without_personal_data(progression: Service) -> None:
    await progression.publish_config()
    player, newcomer = uuid7(), uuid7()
    await progression.deliver(progression.game_finished(player, [100, 100, 100]))  # 90 XP

    response = await levels(progression, [player, newcomer])

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "items": [
            {"user_id": str(player), "level": 1, "xp_in_level": 90},
            {"user_id": str(newcomer), "level": 1, "xp_in_level": 0},  # строки ещё нет
        ]
    }


async def test_hidden_profile_is_skipped_and_duplicates_collapse(progression: Service) -> None:
    await progression.publish_config()
    shown, hidden = uuid7(), uuid7()
    for user in (shown, hidden):
        await progression.deliver(progression.game_finished(user, [100]))
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            update(progress).where(progress.c.user_id == hidden).values(hidden=True)
        )

    body = (await levels(progression, [shown, hidden, shown])).json()

    assert [i["user_id"] for i in body["items"]] == [str(shown)]


async def test_only_service_token_with_levels_read_scope_is_accepted(progression: Service) -> None:
    await progression.publish_config()
    params = [("user_ids", str(uuid7()))]
    anonymous = await progression.client.get(PATH, params=params)
    assert_problem(anonymous, 401, "auth.unauthenticated")
    # Пользовательский JWT (и гостя) и токен другому сервису - 401.
    for token in (
        progression.issuer.issue(),
        progression.issuer.issue_guest(),
        service(progression, audience="game"),
    ):
        response = await progression.client.get(
            PATH, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401, response.text
    no_scope = await levels(progression, [uuid7()], service(progression, scopes=("other.read",)))
    assert_problem(no_scope, 403, "auth.forbidden")
    # Публичной ручки нет.
    public = await progression.client.get("/v1/levels", params=params, headers=progression.user())
    assert public.status_code == 404


async def test_user_ids_are_capped_and_rate_limited(progression: Service) -> None:
    await progression.publish_config()
    max_users = progression.container.settings.progression.levels_max_users
    over_setting = await levels(progression, [uuid7() for _ in range(max_users + 1)])
    problem = assert_problem(over_setting, 422, "progression.too_many_users")
    assert problem["details"] == {"given": max_users + 1, "max": max_users}
    over_schema = await levels(progression, [uuid7() for _ in range(101)])
    assert over_schema.status_code == 422
    assert (await levels(progression, [])).status_code == 422
    # Дубли не считаются против потолка.
    one = uuid7()
    assert (await levels(progression, [one] * (max_users + 5))).status_code == 200

    progression.container.queries._levels_per_min = 1
    limited = await levels(progression, [one])
    assert_problem(limited, 429, "progression.levels_rate_limited")
    assert "retry-after" in limited.headers


async def test_no_published_config_is_unavailable(progression: Service) -> None:
    response = await levels(progression, [uuid7()])
    assert response.status_code == 503, response.text
