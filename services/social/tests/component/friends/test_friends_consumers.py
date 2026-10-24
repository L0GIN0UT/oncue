"""Consumers друзей: ники, «в матче», удаление аккаунта (поток F1; ADR 0050)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from social_service.infrastructure.db.tables_friends import (
    blocks,
    friend_codes,
    friend_requests,
    friendships,
    in_match_proj,
    users_proj,
)
from sqlalchemy import select

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, load_example

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py


def event_of(name: str, **payload: Any) -> Any:
    base = load_example(name)
    return base.model_copy(update={"payload": {**base.payload, **payload}})


async def nickname_of(social: Social, user_id: UUID) -> str | None:
    assert social.infra.sessions is not None
    async with social.infra.sessions() as session:
        return await session.scalar(
            select(users_proj.c.nickname).where(users_proj.c.user_id == user_id)
        )


async def test_registered_and_changed_events_fill_projection_older_versions_are_dropped(
    social: Social,
) -> None:
    uid = uuid7()
    await social.register("Neo_2026", uid, version=1)
    changed = event_of(
        "identity.UserProfileBasicsChanged", user_id=str(uid), nickname="Neo_new", version=3
    )
    assert await social.deliver(changed) is Outcome.PROCESSED
    assert await nickname_of(social, uid) == "Neo_new"

    stale = event_of(
        "identity.UserProfileBasicsChanged", user_id=str(uid), nickname="Neo_old", version=2
    )
    assert await social.deliver(stale) is Outcome.PROCESSED
    same = event_of(
        "identity.UserProfileBasicsChanged", user_id=str(uid), nickname="Neo_same", version=3
    )
    assert await social.deliver(same) is Outcome.PROCESSED
    assert await nickname_of(social, uid) == "Neo_new"


async def test_same_event_twice_is_a_duplicate(social: Social) -> None:
    envelope = event_of("identity.UserRegistered", nickname="Dupe_1")
    assert await social.deliver(envelope) is Outcome.PROCESSED
    assert await social.deliver(envelope) is Outcome.DUPLICATE


async def test_changed_nickname_is_found_by_new_name_only(social: Social) -> None:
    await social.publish_config()
    alice = await social.register("Alice")
    bobby = await social.register("Bobby")
    await social.deliver(
        event_of(
            "identity.UserProfileBasicsChanged", user_id=str(bobby), nickname="Robert", version=2
        )
    )
    old = await social.client.post(
        "/v1/me/friend-requests",
        json={"nickname": "Bobby"},
        headers={**social.headers(alice), **social.key()},
    )
    assert old.status_code == 404
    new = await social.client.post(
        "/v1/me/friend-requests",
        json={"nickname": "Robert"},
        headers={**social.headers(alice), **social.key()},
    )
    assert new.status_code == 201


async def test_broken_payload_goes_to_dlq(social: Social) -> None:
    broken = event_of("identity.UserRegistered", nickname="x")  # короче 3 символов
    assert await social.deliver(broken) is Outcome.DEAD
    assert social.dead_letters.letters[-1]["reason"] == "permanent"


async def test_match_started_and_finished_maintain_in_match_projection(social: Social) -> None:
    a, b, match_id = uuid7(), uuid7(), uuid7()
    players = [{"slot": 0, "user_id": str(a)}, {"slot": 1, "user_id": str(b)}]
    started = event_of("match.MatchStarted", match_id=str(match_id), players=players)
    assert await social.deliver(started) is Outcome.PROCESSED
    assert {r.user_id for r in await social.rows(in_match_proj)} == {a, b}
    assert await social.deliver(started) is Outcome.DUPLICATE

    # повтор того же матча с новым event_id ничего не двоит
    again = started.model_copy(update={"event_id": uuid7()})
    assert await social.deliver(again) is Outcome.PROCESSED
    assert len(await social.rows(in_match_proj)) == 2

    await social.deliver(event_of("match.MatchFinished", match_id=str(match_id)))
    assert await social.rows(in_match_proj) == []


async def test_user_deleted_erases_friendships_requests_codes_blocks(social: Social) -> None:
    await social.publish_config()
    alice, bobby, carol = (
        await social.register("Alice"),
        await social.register("Bobby"),
        await social.register("Carol"),
    )
    await social.befriend(alice, bobby)
    await social.client.get("/v1/me/friend-code", headers=social.headers(alice))
    await social.client.post(
        "/v1/me/friend-requests",
        json={"nickname": "Carol"},
        headers={**social.headers(alice), **social.key()},
    )
    await social.client.put(f"/v1/me/blocks/{carol}", headers=social.headers(alice))
    await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(carol))
    assert len(await social.rows(blocks)) == 2 and len(await social.rows(friendships)) == 1

    deleted = event_of("identity.UserDeleted", user_id=str(alice), version=9)
    assert await social.deliver(deleted) is Outcome.PROCESSED
    for table in (friendships, friend_requests, friend_codes, blocks):
        assert await social.rows(table) == [], table.name
    assert await nickname_of(social, alice) is None
    assert await nickname_of(social, bobby) == "Bobby"

    [removed] = [
        e
        for e in await social.outbox("social.FriendRemoved")
        if e.payload["reason"] == "account_deleted"
    ]
    assert_event_matches_schema(removed)
    assert removed.payload["removed_by"] == str(alice)

    # повтор удаления и новый event_id - без новых событий и ошибок
    before = len(await social.outbox())
    again = deleted.model_copy(update={"event_id": uuid7()})
    assert await social.deliver(again) is Outcome.PROCESSED
    assert len(await social.outbox()) == before
