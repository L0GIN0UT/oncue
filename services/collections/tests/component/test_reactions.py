"""Лайки и избранное (D13): идемпотентность, событие только при новом лайке, свой сборник."""

from __future__ import annotations

from typing import Any

import pytest

from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component


async def test_like_is_idempotent_and_publishes_only_new_like(svc: Any) -> None:
    cid = (await svc.published(svc.user()))["collection_id"]
    fan = svc.user()
    url = f"/v1/collections/{cid}/like"
    first = await svc.client.put(url, headers=fan.mutate())
    assert first.json() == {"liked": True, "likes_count": 1}
    again = await svc.client.put(url, headers=fan.mutate())
    assert again.json() == {"liked": True, "likes_count": 1}
    (event,) = await svc.events("collections.CollectionLiked", cid)
    assert_event_matches_schema(event)
    assert event.payload["user_id"] == str(fan.user_id) and event.payload["likes_count"] == 1
    # version агрегата общая: публикация — 1, лайк — 2.
    assert event.payload["version"] == 2

    other = svc.user()
    assert (await svc.client.put(url, headers=other.mutate())).json()["likes_count"] == 2
    removed = await svc.client.delete(url, headers=fan.mutate())
    assert removed.json() == {"liked": False, "likes_count": 1}
    assert (await svc.client.delete(url, headers=fan.mutate())).json()["likes_count"] == 1
    assert len(await svc.events("collections.CollectionLiked", cid)) == 2

    card = (await svc.client.get(f"/v1/collections/{cid}", headers=other.headers)).json()
    assert card["likes_count"] == 1 and card["me"]["liked"] is True


async def test_author_cannot_like_own_collection(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.published(author))["collection_id"]
    response = await svc.client.put(f"/v1/collections/{cid}/like", headers=author.mutate())
    assert_problem(response, 403, "collections.own_collection")
    # Своё в избранное — можно.
    favorite = await svc.client.put(f"/v1/collections/{cid}/favorite", headers=author.mutate())
    assert favorite.json() == {"favorite": True}


async def test_reactions_on_invisible_collections_are_not_found(svc: Any) -> None:
    author, fan = svc.user(), svc.user()
    draft = (await svc.create(author))["collection_id"]
    hidden = (await svc.published(author))["collection_id"]
    moderator = svc.admin()
    await svc.client.post(
        f"/admin/v1/collections/{hidden}/hide", json={"reason": "спам"}, headers=moderator.mutate()
    )
    for cid in (draft, hidden):
        for method, path in (("PUT", "like"), ("DELETE", "like"), ("PUT", "favorite")):
            response = await svc.client.request(
                method, f"/v1/collections/{cid}/{path}", headers=fan.mutate()
            )
            assert_problem(response, 404, "collections.not_found")


async def test_favorites_page_with_cursor_hides_unpublished(svc: Any) -> None:
    fan = svc.user()
    ids = [(await svc.published(svc.user()))["collection_id"] for _ in range(3)]
    for cid in ids:
        svc.clock.advance(seconds=1)
        assert (
            await svc.client.put(f"/v1/collections/{cid}/favorite", headers=fan.mutate())
        ).json() == {"favorite": True}
    first = (
        await svc.client.get("/v1/me/favorites", params={"limit": 2}, headers=fan.headers)
    ).json()
    assert [i["collection_id"] for i in first["items"]] == [ids[2], ids[1]]
    second = (
        await svc.client.get(
            "/v1/me/favorites",
            params={"limit": 2, "cursor": first["next_cursor"]},
            headers=fan.headers,
        )
    ).json()
    assert [i["collection_id"] for i in second["items"]] == [ids[0]]
    assert second["next_cursor"] is None

    moderator = svc.admin()
    await svc.client.post(
        f"/admin/v1/collections/{ids[2]}/hide", json={"reason": "спам"}, headers=moderator.mutate()
    )
    page = (await svc.client.get("/v1/me/favorites", headers=fan.headers)).json()
    assert ids[2] not in {i["collection_id"] for i in page["items"]}
    assert (
        await svc.client.delete(f"/v1/collections/{ids[0]}/favorite", headers=fan.mutate())
    ).json() == {"favorite": False}
    card = (await svc.client.get(f"/v1/collections/{ids[1]}", headers=fan.headers)).json()
    assert card["me"]["favorite"] is True


async def test_reactions_rate_limit(service_factory: Any) -> None:
    svc = await service_factory(reactions_per_min=2)
    cid = (await svc.published(svc.user()))["collection_id"]
    fan = svc.user()
    for _ in range(2):
        await svc.client.put(f"/v1/collections/{cid}/favorite", headers=fan.mutate())
    response = await svc.client.put(f"/v1/collections/{cid}/like", headers=fan.mutate())
    body = assert_problem(response, 429, "collections.rate_limited")
    assert body["details"]["retry_after"] >= 1
