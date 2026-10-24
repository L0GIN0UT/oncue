"""Присутствие, статусы друзей и приглашения в комнату (поток F1; ADR 0050)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem, load_example

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py


@pytest.fixture
async def friends(social: Social) -> tuple[UUID, UUID]:
    await social.publish_config()
    alice, bobby = await social.register("Alice"), await social.register("Bobby")
    await social.befriend(alice, bobby)
    return alice, bobby


async def invite(
    social: Social, sender: UUID, to: UUID, key: str | None = None, **extra: Any
) -> Any:
    headers = {
        **social.headers(sender, **extra),
        "Idempotency-Key": key or social.key()["Idempotency-Key"],
    }
    return await social.client.post(
        f"/v1/me/friends/{to}/invite", json={"room_code": "K7M2QX"}, headers=headers
    )


async def friend_status(social: Social, viewer: UUID, friend: UUID) -> str:
    listing = (await social.client.get("/v1/me/friends", headers=social.headers(viewer))).json()
    [item] = [i for i in listing["items"] if i["user_id"] == str(friend)]
    return item["status"]


# ---------------------------------------------------------------------- presence


async def test_heartbeat_sets_key_with_ttl_and_makes_friend_online(
    social: Social, friends: tuple[UUID, UUID]
) -> None:
    alice, bobby = friends
    assert await friend_status(social, alice, bobby) == "offline"
    response = await social.client.post("/v1/me/presence", headers=social.headers(bobby))
    assert response.status_code == 204
    ttl = await social.infra.redis.ttl(f"social:presence:{bobby}")
    assert 0 < ttl <= 75  # presence.ttl_s из конфига
    assert await friend_status(social, alice, bobby) == "online"
    assert await friend_status(social, bobby, alice) == "offline"


async def test_in_match_status_follows_match_events(
    social: Social, friends: tuple[UUID, UUID]
) -> None:
    alice, bobby = friends
    await social.client.post("/v1/me/presence", headers=social.headers(bobby))
    match_id = uuid7()
    started = load_example("match.MatchStarted")
    players = [{"slot": 0, "user_id": str(bobby)}, {"slot": 1, "user_id": str(uuid7())}]
    started = started.model_copy(
        update={"payload": {**started.payload, "match_id": str(match_id), "players": players}}
    )
    await social.deliver(started)
    assert await friend_status(social, alice, bobby) == "in_match"

    finished = load_example("match.MatchFinished")
    finished = finished.model_copy(
        update={"payload": {**finished.payload, "match_id": str(match_id)}}
    )
    await social.deliver(finished)
    assert await friend_status(social, alice, bobby) == "online"


async def test_heartbeat_is_rate_limited_per_user(
    social: Social, friends: tuple[UUID, UUID]
) -> None:
    _, bobby = friends
    burst = social.container.settings.social.presence_burst
    for _ in range(burst):
        assert (
            await social.client.post("/v1/me/presence", headers=social.headers(bobby))
        ).status_code == 204
    limited = await social.client.post("/v1/me/presence", headers=social.headers(bobby))
    assert_problem(limited, 429, "social.presence_rate_limited")
    assert "retry-after" in limited.headers


# ---------------------------------------------------------------------- приглашения


async def test_friend_is_invited_event_matches_contract(
    social: Social, friends: tuple[UUID, UUID]
) -> None:
    alice, bobby = friends
    response = await invite(social, alice, bobby)
    assert response.status_code == 202
    assert response.json()["repeated"] is False
    [event] = await social.outbox("social.RoomInviteSent")
    assert_event_matches_schema(event)
    assert event.payload["to_user_id"] == str(bobby)
    assert event.payload["from_nickname"] == "Alice"
    assert event.payload["room_code"] == "K7M2QX"
    assert event.payload["invite_id"] == response.json()["invite_id"]
    assert event.aggregate_id == response.json()["invite_id"]


async def test_invite_is_idempotent_by_key(social: Social, friends: tuple[UUID, UUID]) -> None:
    alice, bobby = friends
    first = await invite(social, alice, bobby, key="invite-key-0001")
    second = await invite(social, alice, bobby, key="invite-key-0001")
    assert second.status_code == 202
    assert second.json() == {"invite_id": first.json()["invite_id"], "repeated": True}
    assert len(await social.outbox("social.RoomInviteSent")) == 1


async def test_only_friends_can_be_invited(social: Social, friends: tuple[UUID, UUID]) -> None:
    alice, _ = friends
    stranger = await social.register("Stranger")
    for target in (stranger, uuid7(), alice):  # не друг, неизвестный, сам себя
        assert_problem(await invite(social, alice, target), 403, "social.invite_forbidden")
    assert await social.outbox("social.RoomInviteSent") == []


async def test_block_in_either_direction_forbids_invites(social: Social) -> None:
    await social.publish_config()
    alice, bobby = await social.register("Alice"), await social.register("Bobby")
    await social.befriend(alice, bobby)
    await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(bobby))
    assert_problem(await invite(social, alice, bobby), 403, "social.invite_forbidden")
    assert_problem(await invite(social, bobby, alice), 403, "social.invite_forbidden")


async def test_online_sanction_forbids_invites(social: Social, friends: tuple[UUID, UUID]) -> None:
    alice, bobby = friends
    banned = await invite(social, alice, bobby, sanctions=int(Sanction.ONLINE))
    assert_problem(banned, 403, "social.invite_forbidden")
    # другие санкции приглашениям не мешают
    ok = await invite(social, alice, bobby, sanctions=int(Sanction.RANKED | Sanction.COMMENTS))
    assert ok.status_code == 202


async def test_invites_per_minute_limit_and_replay_does_not_consume_it(social: Social) -> None:
    await social.publish_config(friends__invites_per_minute=2)
    alice, bobby = await social.register("Alice"), await social.register("Bobby")
    await social.befriend(alice, bobby)
    assert (await invite(social, alice, bobby, key="invite-key-0001")).status_code == 202
    assert (await invite(social, alice, bobby, key="invite-key-0001")).status_code == 202  # повтор
    assert (await invite(social, alice, bobby, key="invite-key-0002")).status_code == 202
    limited = await invite(social, alice, bobby, key="invite-key-0003")
    assert_problem(limited, 429, "social.invites_rate_limited")
    assert int(limited.headers["retry-after"]) > 0
    assert len(await social.outbox("social.RoomInviteSent")) == 2


async def test_invite_without_key_or_with_bad_room_code_is_rejected(
    social: Social, friends: tuple[UUID, UUID]
) -> None:
    alice, bobby = friends
    no_key = await social.client.post(
        f"/v1/me/friends/{bobby}/invite",
        json={"room_code": "K7M2QX"},
        headers=social.headers(alice),
    )
    assert_problem(no_key, 422, "idempotency.key_required")
    bad = await social.client.post(
        f"/v1/me/friends/{bobby}/invite",
        json={"room_code": "bad code!"},
        headers={**social.headers(alice), **social.key()},
    )
    assert bad.status_code == 422
