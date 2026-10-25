"""Пакетное отношение к игрокам и ник в relation (ADR 0069): для bff, итог матча и профиль."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py
URL = "/v1/players/relations"


async def _privacy(social: Social, user: UUID, **values: Any) -> None:
    body = {"profile_public": True, "stats_public": True, "friend_requests": "all", **values}
    response = await social.client.put("/v1/me/privacy", json=body, headers=social.headers(user))
    assert response.status_code == 200, response.text


async def _batch(social: Social, viewer: UUID, ids: list[UUID]) -> Any:
    return await social.client.get(
        URL, params=[("user_ids", str(i)) for i in ids], headers=social.headers(viewer)
    )


@pytest.fixture
async def world(social: Social) -> dict[str, UUID]:
    await social.publish_config()
    return {
        "me": await social.register("Alice"),
        "stranger": await social.register("Bobby"),
        "friend": await social.register("Friend"),
        "closed": await social.register("Closed"),
        "hidden": await social.register("Hidden"),
        "blocker": await social.register("Blocker"),
    }


async def test_batch_answers_per_player_in_one_call(social: Social, world: dict[str, UUID]) -> None:
    me = world["me"]
    await social.befriend(me, world["friend"])
    await _privacy(social, world["closed"], friend_requests="none")
    await _privacy(social, world["hidden"], profile_public=False, stats_public=False)
    await social.client.put(f"/v1/me/blocks/{me}", headers=social.headers(world["blocker"]))
    ids = [world[k] for k in ("stranger", "friend", "closed", "hidden", "blocker")] + [uuid7()]

    response = await _batch(social, me, ids)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    items = {UUID(i["user_id"]): i for i in response.json()["items"]}
    assert set(items) == set(ids)
    assert items[world["stranger"]]["can_send_request"] is True
    assert items[world["stranger"]]["nickname"] == "Bobby"
    assert items[world["friend"]]["is_friend"] is True
    assert items[world["friend"]]["can_send_request"] is False
    assert items[world["closed"]]["can_send_request"] is False  # приватность «никто не зовёт»
    assert items[world["closed"]]["profile_visible"] is True
    # скрытый, заблокировавший и неизвестный неразличимы и ника не выдают
    for key in (world["hidden"], world["blocker"], ids[-1]):
        assert items[key]["profile_visible"] is False
        assert items[key]["can_send_request"] is False
        assert items[key]["nickname"] is None


async def test_duplicates_collapse_and_order_follows_request(
    social: Social, world: dict[str, UUID]
) -> None:
    a, b = world["stranger"], world["friend"]
    response = await _batch(social, world["me"], [b, a, b])
    assert [UUID(i["user_id"]) for i in response.json()["items"]] == [b, a]


async def test_limit_and_validation(social: Social, world: dict[str, UUID]) -> None:
    me = world["me"]
    assert (await _batch(social, me, [])).status_code == 422
    too_many = await _batch(social, me, [uuid7() for _ in range(51)])
    assert too_many.status_code == 422
    bad = await social.client.get(URL, params={"user_ids": "x"}, headers=social.headers(me))
    assert bad.status_code == 422


async def test_batch_requires_an_account(social: Social, world: dict[str, UUID]) -> None:
    anonymous = await social.client.get(URL, params={"user_ids": str(uuid7())})
    assert_problem(anonymous, 401, "auth.unauthenticated")
    guest = await social.client.get(
        URL,
        params={"user_ids": str(uuid7())},
        headers=social.headers(world["me"], guest=True),
    )
    assert_problem(guest, 403, "auth.guest_forbidden")


async def test_single_relation_carries_nickname_only_when_visible(
    social: Social, world: dict[str, UUID]
) -> None:
    me = world["me"]
    await _privacy(social, world["hidden"], profile_public=False, stats_public=False)

    async def relation(target: UUID) -> Any:
        r = await social.client.get(f"/v1/players/{target}/relation", headers=social.headers(me))
        assert r.status_code == 200, r.text
        return r.json()

    assert (await relation(world["stranger"]))["nickname"] == "Bobby"
    assert (await relation(me))["nickname"] == "Alice"
    assert (await relation(world["hidden"]))["nickname"] is None
    assert (await relation(uuid7()))["nickname"] is None


async def _count_sql(social: Social, viewer: UUID, ids: list[UUID]) -> int:
    """Сколько SQL-запросов выполняет вызов пакета (счётчик на уровне движка)."""
    engine = social.infra.engine.sync_engine
    statements: list[str] = []

    def on_execute(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        response = await _batch(social, viewer, ids)
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    assert response.status_code == 200, response.text
    return len(statements)


async def test_sql_count_does_not_grow_with_batch_size(
    social: Social, world: dict[str, UUID]
) -> None:
    me = world["me"]
    small = [world["stranger"]]
    many = [await social.register(f"Player{i}") for i in range(30)]
    await _count_sql(social, me, small)  # прогрев (кэш конфига/подготовленные запросы)
    few_sql = await _count_sql(social, me, small)
    many_sql = await _count_sql(social, me, many)
    assert many_sql == few_sql


async def test_batch_is_rate_limited_per_user(social: Social, world: dict[str, UUID]) -> None:
    me, other = world["me"], world["stranger"]
    limit = social.container.settings.social.relations_per_minute
    for _ in range(limit):
        assert (await _batch(social, me, [other])).status_code == 200
    limited = await _batch(social, me, [other])
    assert_problem(limited, 429, "social.relations_rate_limited")
    assert "retry-after" in limited.headers
    # лимит на пользователя: другой игрок не затронут
    assert (await _batch(social, other, [me])).status_code == 200
