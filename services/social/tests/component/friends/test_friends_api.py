"""Друзья: заявки, дружба, блокировки, коды, приватность (поток F1; ADR 0050)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from social_service.infrastructure.db.tables_friends import (
    blocks,
    friend_requests,
    friendships,
)

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py
REQUESTS = "/v1/me/friend-requests"


async def send(social: Social, sender: UUID, **target: str) -> Any:
    return await social.client.post(
        REQUESTS, json=target, headers={**social.headers(sender), **social.key()}
    )


@pytest.fixture
async def duo(social: Social) -> tuple[UUID, UUID]:
    await social.publish_config()
    return await social.register("Alice"), await social.register("Bobby")


# ---------------------------------------------------------------------- доступ


async def test_guest_is_forbidden_and_anonymous_is_unauthorized(social: Social) -> None:
    await social.publish_config()
    guest = social.headers(uuid7(), guest=True)
    assert_problem(
        await social.client.get("/v1/me/friends", headers=guest), 403, "auth.guest_forbidden"
    )
    assert_problem(await social.client.get("/v1/me/friends"), 401, "auth.unauthenticated")


async def test_private_responses_are_not_cacheable(social: Social, duo: tuple[UUID, UUID]) -> None:
    alice, _ = duo
    for path in (
        "/me/friends",
        "/me/friend-requests",
        "/me/friend-code",
        "/me/blocks",
        "/me/privacy",
    ):
        response = await social.client.get(f"/v1{path}", headers=social.headers(alice))
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "private, no-store", path


# ---------------------------------------------------------------------- заявки и дружба


async def test_request_accept_makes_friends_and_publishes_events(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    sent = await send(social, alice, nickname="bobby")  # без учёта регистра
    assert sent.status_code == 201
    assert sent.json()["user_id"] == str(bobby)
    assert sent.json()["nickname"] == "Bobby"

    incoming = (await social.client.get(REQUESTS, headers=social.headers(bobby))).json()
    assert [r["user_id"] for r in incoming["incoming"]] == [str(alice)]
    assert incoming["outgoing"] == []

    accepted = await social.client.post(
        f"{REQUESTS}/{sent.json()['id']}/accept",
        headers={**social.headers(bobby), **social.key()},
    )
    assert accepted.status_code == 200
    friends = (await social.client.get("/v1/me/friends", headers=social.headers(alice))).json()
    assert [(f["user_id"], f["status"]) for f in friends["items"]] == [(str(bobby), "offline")]
    assert friends["total"] == 1

    for envelope in await social.outbox():
        assert_event_matches_schema(envelope)
    # топик в outbox общий для всех типов агрегатов (friendship, invite, block)
    topics = await social.scalar("SELECT array_agg(DISTINCT topic) FROM outbox")
    assert topics == ["social.social.v1"]
    [requested] = await social.outbox("social.FriendRequested")
    assert requested.payload["from_nickname"] == "Alice"
    [added] = await social.outbox("social.FriendAdded")
    low, high = sorted([alice, bobby], key=lambda u: u.int)
    assert (added.payload["user_a"], added.payload["user_b"]) == (str(low), str(high))
    assert added.payload["requester_id"] == str(alice)
    assert added.payload["accepter_nickname"] == "Bobby"


async def test_request_by_code_and_old_code_stops_working(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    code = (await social.client.get("/v1/me/friend-code", headers=social.headers(bobby))).json()
    assert len(code["code"]) == 8
    again = (await social.client.get("/v1/me/friend-code", headers=social.headers(bobby))).json()
    assert again["code"] == code["code"]  # стабилен до перевыпуска

    new = await social.client.post(
        "/v1/me/friend-code/regenerate", headers={**social.headers(bobby), **social.key()}
    )
    assert new.status_code == 200
    assert new.json()["code"] != code["code"]

    assert_problem(await send(social, alice, code=code["code"]), 404, "social.player_not_found")
    ok = await send(social, alice, code=new.json()["code"])
    assert ok.status_code == 201
    assert ok.json()["user_id"] == str(bobby)


async def test_regenerate_is_idempotent_by_key(social: Social, duo: tuple[UUID, UUID]) -> None:
    _, bobby = duo
    headers = {**social.headers(bobby), **social.key()}
    first = await social.client.post("/v1/me/friend-code/regenerate", headers=headers)
    second = await social.client.post("/v1/me/friend-code/regenerate", headers=headers)
    assert first.json() == second.json()


async def test_request_is_idempotent_by_key(social: Social, duo: tuple[UUID, UUID]) -> None:
    alice, _ = duo
    headers = {**social.headers(alice), **social.key()}
    first = await social.client.post(REQUESTS, json={"nickname": "Bobby"}, headers=headers)
    second = await social.client.post(REQUESTS, json={"nickname": "Bobby"}, headers=headers)
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert len(await social.outbox("social.FriendRequested")) == 1
    assert len(await social.rows(friend_requests)) == 1


async def test_unknown_hidden_and_blocking_players_are_indistinguishable(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, _ = duo
    hidden = await social.register("Hidden")
    blocker = await social.register("Blocker")
    await social.client.put(
        "/v1/me/privacy",
        json={"profile_public": True, "stats_public": True, "friend_requests": "none"},
        headers=social.headers(hidden),
    )
    await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(blocker))

    answers = []
    for target in ("Nobody", "Hidden", "Blocker"):
        response = await send(social, alice, nickname=target)
        assert_problem(response, 404, "social.player_not_found")
        answers.append((response.status_code, response.json()["code"], response.json()["title"]))
    assert len(set(answers)) == 1
    assert await social.rows(friend_requests) == []


async def test_request_by_user_id_is_idempotent_and_matches_nickname(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    headers = {**social.headers(alice), **social.key()}
    first = await social.client.post(REQUESTS, json={"user_id": str(bobby)}, headers=headers)
    second = await social.client.post(REQUESTS, json={"user_id": str(bobby)}, headers=headers)
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json() == second.json()
    assert first.json()["user_id"] == str(bobby)
    assert len(await social.rows(friend_requests)) == 1
    assert_problem(await send(social, alice, user_id=str(bobby)), 409, "social.request_pending")


async def test_request_by_user_id_unknown_hidden_blocking_look_the_same(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, _ = duo
    hidden = await social.register("Hidden")
    blocker = await social.register("Blocker")
    await social.client.put(
        "/v1/me/privacy",
        json={"profile_public": True, "stats_public": True, "friend_requests": "none"},
        headers=social.headers(hidden),
    )
    await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(blocker))
    answers = []
    for target in (uuid7(), hidden, blocker, alice):
        response = await send(social, alice, user_id=str(target))
        assert_problem(response, 404, "social.player_not_found")
        answers.append((response.status_code, response.json()["code"], response.json()["title"]))
    assert len(set(answers)) == 1
    assert await social.rows(friend_requests) == []


async def test_cannot_request_self_and_duplicates_conflict(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    assert_problem(await send(social, alice, nickname="Alice"), 404, "social.player_not_found")
    assert (await send(social, alice, nickname="Bobby")).status_code == 201
    assert_problem(await send(social, alice, nickname="Bobby"), 409, "social.request_pending")
    await social.befriend(bobby, await social.register("Third"))  # посторонняя дружба не мешает


async def test_already_friends_conflicts(social: Social, duo: tuple[UUID, UUID]) -> None:
    alice, bobby = duo
    await social.befriend(alice, bobby)
    assert_problem(await send(social, alice, nickname="Bobby"), 409, "social.already_friends")
    assert_problem(await send(social, bobby, nickname="Alice"), 409, "social.already_friends")


async def test_counter_request_merges_into_friendship(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    first = await send(social, alice, nickname="Bobby")
    merged = await send(social, bobby, nickname="Alice")
    assert merged.status_code == 201
    assert merged.json()["id"] == first.json()["id"]  # та же заявка, закрытая слиянием
    assert len(await social.rows(friendships)) == 1
    [added] = await social.outbox("social.FriendAdded")
    assert added.payload["requester_id"] == str(alice)
    assert added.payload["accepter_id"] == str(bobby)


async def test_simultaneous_counter_requests_make_exactly_one_friendship(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    results = await asyncio.gather(
        send(social, alice, nickname="Bobby"), send(social, bobby, nickname="Alice")
    )
    assert [r.status_code for r in results] == [201, 201]
    assert len(await social.rows(friendships)) == 1
    assert len(await social.outbox("social.FriendAdded")) == 1
    pending = [r for r in await social.rows(friend_requests) if r.status == "pending"]
    assert pending == []


async def test_double_accept_makes_exactly_one_friendship(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    request_id = (await send(social, alice, nickname="Bobby")).json()["id"]
    url = f"{REQUESTS}/{request_id}/accept"
    results = await asyncio.gather(
        *(
            social.client.post(url, headers={**social.headers(bobby), **social.key()})
            for _ in range(5)
        )
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1
    assert set(codes) <= {200, 409}
    assert all(
        r.json()["code"] == "social.request_not_pending" for r in results if r.status_code == 409
    )
    assert len(await social.rows(friendships)) == 1
    assert len(await social.outbox("social.FriendAdded")) == 1


async def test_accept_with_same_key_replays_same_answer(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    request_id = (await send(social, alice, nickname="Bobby")).json()["id"]
    headers = {**social.headers(bobby), **social.key()}
    first = await social.client.post(f"{REQUESTS}/{request_id}/accept", headers=headers)
    second = await social.client.post(f"{REQUESTS}/{request_id}/accept", headers=headers)
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()


async def test_only_addressee_can_accept_or_decline_and_only_sender_can_cancel(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    stranger = await social.register("Stranger")
    request_id = (await send(social, alice, nickname="Bobby")).json()["id"]
    for who in (alice, stranger):  # отправитель и посторонний не адресаты
        for action in ("accept", "decline"):
            response = await social.client.post(
                f"{REQUESTS}/{request_id}/{action}", headers={**social.headers(who), **social.key()}
            )
            assert_problem(response, 404, "social.request_not_found")
    assert_problem(
        await social.client.delete(f"{REQUESTS}/{request_id}", headers=social.headers(bobby)),
        404,
        "social.request_not_found",
    )
    assert (
        await social.client.delete(f"{REQUESTS}/{request_id}", headers=social.headers(alice))
    ).status_code == 204
    # повтор отзыва и принятие отозванной
    assert (
        await social.client.delete(f"{REQUESTS}/{request_id}", headers=social.headers(alice))
    ).status_code == 204
    assert_problem(
        await social.client.post(
            f"{REQUESTS}/{request_id}/accept", headers={**social.headers(bobby), **social.key()}
        ),
        409,
        "social.request_not_pending",
    )


async def test_decline_lets_sender_ask_again(social: Social, duo: tuple[UUID, UUID]) -> None:
    alice, bobby = duo
    request_id = (await send(social, alice, nickname="Bobby")).json()["id"]
    declined = await social.client.post(
        f"{REQUESTS}/{request_id}/decline", headers={**social.headers(bobby), **social.key()}
    )
    assert declined.status_code == 204
    assert (await send(social, alice, nickname="Bobby")).status_code == 201


async def test_remove_friend_is_idempotent_and_publishes_once(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    await social.befriend(alice, bobby)
    for _ in range(2):
        response = await social.client.delete(
            f"/v1/me/friends/{bobby}", headers=social.headers(alice)
        )
        assert response.status_code == 204
    assert await social.rows(friendships) == []
    [removed] = await social.outbox("social.FriendRemoved")
    assert (removed.payload["reason"], removed.payload["removed_by"]) == ("removed", str(alice))
    assert_event_matches_schema(removed)


async def test_friend_list_is_only_the_owners(social: Social, duo: tuple[UUID, UUID]) -> None:
    alice, bobby = duo
    await social.befriend(alice, bobby)
    stranger = await social.register("Stranger")
    seen = (await social.client.get("/v1/me/friends", headers=social.headers(stranger))).json()
    assert seen == {"items": [], "total": 0}
    # чужой id в пути не даёт чужого списка: ручек «список друзей игрока» нет
    other = await social.client.get(
        f"/v1/players/{alice}/friends", headers=social.headers(stranger)
    )
    assert other.status_code == 404


# ---------------------------------------------------------------------- лимиты


async def test_daily_request_limit_and_friends_limit(social: Social) -> None:
    await social.publish_config(friends__requests_per_day=2, friends__max_friends=1)
    alice = await social.register("Alice")
    targets = [await social.register(f"Player{i}") for i in range(3)]
    assert (await send(social, alice, nickname="Player0")).status_code == 201
    assert (await send(social, alice, nickname="Player1")).status_code == 201
    limited = await send(social, alice, nickname="Player2")
    assert_problem(limited, 429, "social.friend_requests_daily_limit")
    assert "retry-after" in limited.headers
    # сутки прошли
    social.clock.advance(hours=25)
    social.container.config.invalidate()  # type: ignore[attr-defined]
    assert (await send(social, alice, nickname="Player2")).status_code == 201

    # предел друзей: у отправителя - при отправке, у любого из двоих - при принятии
    request_id = (await social.client.get(REQUESTS, headers=social.headers(targets[0]))).json()[
        "incoming"
    ][0]["id"]
    await social.client.post(
        f"{REQUESTS}/{request_id}/accept", headers={**social.headers(targets[0]), **social.key()}
    )
    assert_problem(await send(social, alice, nickname="Alice2"), 404, "social.player_not_found")
    second = (await social.client.get(REQUESTS, headers=social.headers(targets[1]))).json()[
        "incoming"
    ][0]["id"]
    full = await social.client.post(
        f"{REQUESTS}/{second}/accept", headers={**social.headers(targets[1]), **social.key()}
    )
    assert_problem(full, 409, "social.friends_limit")


async def test_lookup_rate_limit_by_user_and_by_ip(social: Social) -> None:
    await social.publish_config(friends__code_attempts_per_hour=3)
    attacker = await social.register("Attacker")
    for _ in range(3):
        assert_problem(
            await send(social, attacker, nickname="Nobody"), 404, "social.player_not_found"
        )
    limited = await send(social, attacker, nickname="Nobody")
    assert_problem(limited, 429, "social.lookup_rate_limited")
    assert int(limited.headers["retry-after"]) > 0
    # лимит по IP: другой игрок с того же адреса исчерпал общее окно
    other = await social.register("Other")
    assert_problem(await send(social, other, code="ABCD2345"), 429, "social.lookup_rate_limited")


async def test_replay_with_same_key_does_not_spend_lookup_budget(social: Social) -> None:
    await social.publish_config(friends__code_attempts_per_hour=2)
    alice = await social.register("Alice")
    await social.register("Bobby")
    headers = {**social.headers(alice), **social.key()}
    for _ in range(5):  # повтор готового ответа не тратит бюджет перебора
        response = await social.client.post(REQUESTS, json={"nickname": "Bobby"}, headers=headers)
        assert response.status_code == 201
    # бюджет цел: после одного выполненного запроса остался ещё один поиск
    assert_problem(await send(social, alice, nickname="Nobody"), 404, "social.player_not_found")
    assert_problem(await send(social, alice, nickname="Nobody"), 429, "social.lookup_rate_limited")


async def test_sender_without_profile_gets_503_before_target_lookup(social: Social) -> None:
    await social.publish_config()
    await social.register("Bobby")
    stranger = uuid7()  # проекции identity для отправителя ещё нет
    for target in ("Bobby", "Nobody"):  # существование цели не раскрывается
        response = await send(social, stranger, nickname=target)
        assert_problem(response, 503, "social.profile_not_ready")


async def test_config_missing_closes_dependent_operations(social: Social) -> None:
    alice = await social.register("Alice")
    response = await send(social, alice, nickname="Bobby")
    assert_problem(response, 503, "social.config_unavailable")
    assert "retry-after" in response.headers
    # операции, не зависящие от конфига, работают
    assert (
        await social.client.get("/v1/me/friends", headers=social.headers(alice))
    ).status_code == 200


# ---------------------------------------------------------------------- блокировки


async def test_block_breaks_friendship_and_pending_requests_and_forbids_both_ways(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    await social.befriend(alice, bobby)
    response = await social.client.put(f"/v1/me/blocks/{bobby}", headers=social.headers(alice))
    assert response.status_code == 204
    assert await social.rows(friendships) == []
    [removed] = await social.outbox("social.FriendRemoved")
    assert removed.payload["reason"] == "blocked"
    [blocked] = await social.outbox("social.UserBlocked")
    assert (blocked.payload["blocker_id"], blocked.payload["blocked_id"]) == (
        str(alice),
        str(bobby),
    )
    assert_event_matches_schema(blocked)

    # повтор не создаёт событий
    again = await social.client.put(f"/v1/me/blocks/{bobby}", headers=social.headers(alice))
    assert again.status_code == 204
    assert len(await social.outbox("social.UserBlocked")) == 1

    # заявки в обе стороны запрещены; со стороны заблокированного ответ - как у неизвестного
    assert_problem(await send(social, bobby, nickname="Alice"), 404, "social.player_not_found")
    assert_problem(await send(social, alice, nickname="Bobby"), 403, "social.blocked_by_you")

    listed = (await social.client.get("/v1/me/blocks", headers=social.headers(alice))).json()
    assert [b["user_id"] for b in listed["items"]] == [str(bobby)]

    # разблокировка: дружба не возвращается, заявки снова возможны; повтор - 204
    for _ in range(2):
        un = await social.client.delete(f"/v1/me/blocks/{bobby}", headers=social.headers(alice))
        assert un.status_code == 204
    assert await social.rows(friendships) == []
    assert await social.rows(blocks) == []
    assert (await send(social, alice, nickname="Bobby")).status_code == 201


async def test_block_cancels_pending_requests_of_the_pair(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    await send(social, alice, nickname="Bobby")
    await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(bobby))
    assert [r.status for r in await social.rows(friend_requests)] == ["cancelled"]
    mine = (await social.client.get(REQUESTS, headers=social.headers(bobby))).json()
    assert mine == {"incoming": [], "outgoing": []}


async def test_block_self_and_unknown_player(social: Social, duo: tuple[UUID, UUID]) -> None:
    alice, _ = duo
    assert_problem(
        await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(alice)),
        422,
        "social.cannot_block_self",
    )
    assert_problem(
        await social.client.put(f"/v1/me/blocks/{uuid7()}", headers=social.headers(alice)),
        404,
        "social.player_not_found",
    )


# ---------------------------------------------------------------------- приватность и relation


async def test_privacy_defaults_replace_and_validation(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, _ = duo
    headers = social.headers(alice)
    default = (await social.client.get("/v1/me/privacy", headers=headers)).json()
    assert default == {"profile_public": True, "stats_public": True, "friend_requests": "all"}
    body = {"profile_public": False, "stats_public": False, "friend_requests": "none"}
    assert (await social.client.put("/v1/me/privacy", json=body, headers=headers)).json() == body
    assert (await social.client.get("/v1/me/privacy", headers=headers)).json() == body
    bad = await social.client.put(
        "/v1/me/privacy", json={**body, "friend_requests": "friends"}, headers=headers
    )
    assert bad.status_code == 422


async def test_relation_is_identical_for_hidden_unknown_and_blocking(
    social: Social, duo: tuple[UUID, UUID]
) -> None:
    alice, bobby = duo
    hidden = await social.register("Hidden")
    blocker = await social.register("Blocker")
    await social.client.put(
        "/v1/me/privacy",
        json={"profile_public": False, "stats_public": False, "friend_requests": "all"},
        headers=social.headers(hidden),
    )
    await social.client.put(f"/v1/me/blocks/{alice}", headers=social.headers(blocker))

    async def relation(target: UUID, viewer: UUID = alice) -> dict[str, Any]:
        response = await social.client.get(
            f"/v1/players/{target}/relation", headers=social.headers(viewer)
        )
        assert response.status_code == 200
        return response.json()

    empty = {
        "profile_visible": False,
        "stats_visible": False,
        "is_friend": False,
        "can_send_request": False,
        "blocked_by_me": False,
        "nickname": None,
    }
    assert await relation(uuid7()) == empty
    assert await relation(hidden) == empty
    assert await relation(blocker) == empty

    assert await relation(bobby) == {
        **empty,
        "profile_visible": True,
        "stats_visible": True,
        "can_send_request": True,
        "nickname": "Bobby",
    }
    await social.befriend(alice, bobby)
    assert (await relation(bobby))["is_friend"] is True
    assert (await relation(bobby))["can_send_request"] is False

    # друзья видят скрытый профиль, зритель сам видит блокировку
    await social.client.put(
        "/v1/me/privacy",
        json={"profile_public": False, "stats_public": False, "friend_requests": "none"},
        headers=social.headers(bobby),
    )
    friend_view = await relation(bobby)
    assert (friend_view["profile_visible"], friend_view["stats_visible"]) == (True, True)
    await social.client.put(f"/v1/me/blocks/{hidden}", headers=social.headers(alice))
    assert (await relation(hidden))["blocked_by_me"] is True
