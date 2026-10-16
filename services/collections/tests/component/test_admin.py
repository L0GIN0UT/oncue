"""Admin-API (K9, D14): кураторские сборники, скрытие, «рекомендуемое», ограничение, аудит."""

from __future__ import annotations

from typing import Any

import pytest

from gv_testkit import assert_event_matches_schema, assert_problem, load_example

pytestmark = pytest.mark.component


def _curated(items: list[Any], **extra: Any) -> dict[str, Any]:
    return {
        "domain": "film",
        "title": "Лучшее 90-х",
        "default_mode_key": "film.collection.frame",
        "items": [{"media_item_id": str(i)} for i in items],
        "reason": "кураторский",
        **extra,
    }


async def test_curated_create_and_update(svc: Any) -> None:
    curator = svc.admin()
    items = await svc.add_items(6)
    created = await svc.client.post(
        "/admin/v1/collections",
        json=_curated(items[:5], tags=["classic"], cover={"preset": "cinema"}),
        headers=curator.mutate(),
    )
    assert created.status_code == 201, created.text
    data = created.json()
    cid = data["collection_id"]
    assert data["kind"] == "curated" and data["status"] == "published" and data["version"] == 1
    assert data["author"]["kind"] == "curator" and len(data["items"]) == 5
    (event,) = await svc.events("collections.CollectionPublished", cid)
    assert_event_matches_schema(event)
    assert event.payload["author_id"] is None and event.payload["kind"] == "curated"

    body = {k: v for k, v in _curated(items).items() if k != "domain"}
    stale = await svc.client.put(
        f"/admin/v1/collections/{cid}",
        json={**body, "base_version": 2, "reason": "правка"},
        headers=curator.mutate(),
    )
    assert_problem(stale, 409, "collections.version_conflict")
    updated = await svc.client.put(
        f"/admin/v1/collections/{cid}",
        json={**body, "base_version": 1, "reason": "правка"},
        headers=curator.mutate(),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2 and len(updated.json()["items"]) == 6
    audit = await svc.sql("SELECT action, reason FROM admin_actions ORDER BY created_at, id")
    assert [a.action for a in audit] == ["collection.curated_create", "collection.curated_update"]


async def test_curated_is_checked_like_ugc(svc: Any) -> None:
    curator = svc.admin()
    music = await svc.add_items(5, domain="music")
    response = await svc.client.post(
        "/admin/v1/collections", json=_curated(music), headers=curator.mutate()
    )
    body = assert_problem(response, 422, "collections.publish_invalid")
    assert {e["code"] for e in body["details"]["errors"]} == {"collections.item_domain_mismatch"}


async def test_admin_cannot_rewrite_ugc(svc: Any) -> None:
    cid = (await svc.published(svc.user()))["collection_id"]
    items = await svc.add_items(5)
    body = {k: v for k, v in _curated(items).items() if k != "domain"}
    response = await svc.client.put(
        f"/admin/v1/collections/{cid}",
        json={**body, "base_version": 1},
        headers=svc.admin().mutate(),
    )
    assert_problem(response, 409, "collections.not_curated")


async def test_hide_and_unhide_publish_updates_once(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.published(author))["collection_id"]
    moderator = svc.admin("admin.access", "collections.unpublish")
    for _ in range(2):
        hidden = await svc.client.post(
            f"/admin/v1/collections/{cid}/hide", json={"reason": "спам"}, headers=moderator.mutate()
        )
        assert hidden.status_code == 200, hidden.text
    out = hidden.json()
    assert out["status"] == "hidden" and out["hidden"]["reason"] == "спам"
    assert out["hidden"]["actor_id"] == str(moderator.user_id)
    events = await svc.events("collections.CollectionUpdated", cid)
    assert [e.payload["status"] for e in events] == ["hidden"]
    assert_event_matches_schema(events[0])

    mine = (await svc.client.get("/v1/me/collections", headers=author.headers)).json()["items"]
    assert mine[0]["hidden_by_moderator"] is True and mine[0]["status"] == "hidden"
    assert_problem(await svc.publish(author, cid, 1), 409, "collections.hidden")
    assert (await svc.client.get("/v1/collections")).json()["items"] == []

    shown = await svc.client.post(
        f"/admin/v1/collections/{cid}/unhide", json={"reason": "ошибка"}, headers=moderator.mutate()
    )
    assert shown.json()["status"] == "published" and shown.json()["hidden"] is None
    events = await svc.events("collections.CollectionUpdated", cid)
    assert [e.payload["status"] for e in events] == ["hidden", "published"]
    assert [e.payload["version"] for e in events] == [2, 3]
    actions = await svc.sql("SELECT action, details FROM admin_actions ORDER BY created_at, id")
    assert [a.action for a in actions] == [
        "collection.hide",
        "collection.hide",
        "collection.unhide",
    ]
    assert [a.details["changed"] for a in actions] == [True, False, True]


async def test_hide_draft_or_missing(svc: Any) -> None:
    moderator = svc.admin()
    cid = (await svc.create(svc.user()))["collection_id"]
    response = await svc.client.post(
        f"/admin/v1/collections/{cid}/hide", json={"reason": "спам"}, headers=moderator.mutate()
    )
    assert_problem(response, 409, "collections.not_published")
    featured = await svc.client.put(
        f"/admin/v1/collections/{cid}/featured",
        json={"featured": True, "reason": "подборка"},
        headers=moderator.mutate(),
    )
    assert_problem(featured, 409, "collections.not_published")


async def test_admin_list_and_detail(svc: Any) -> None:
    author = svc.user()
    nick = f"neo_{author.user_id.hex[-6:]}"
    registered = load_example("identity.UserRegistered")
    payload = {**registered.payload, "user_id": str(author.user_id), "nickname": nick}
    await svc.deliver(registered.model_copy(update={"payload": payload}))

    published = (await svc.published(author, title="Опубликованный"))["collection_id"]
    draft = await svc.create(author, title="Черновик")
    items = await svc.add_items(2)
    await svc.save(
        author,
        draft["collection_id"],
        {
            "title": "Черновик",
            "items": [{"media_item_id": str(i), "custom": {"emoji": "🎬🍿🎥🎭🌟🎻"}} for i in items],
        },
        base=1,
    )
    viewer = svc.admin("admin.access", "collections.view")
    page = (
        await svc.client.get("/admin/v1/collections", params={"q": nick}, headers=viewer.headers)
    ).json()
    assert {i["collection_id"] for i in page["items"]} == {published, draft["collection_id"]}
    assert all(i["author"]["nickname"] == nick for i in page["items"])
    drafts = (
        await svc.client.get(
            "/admin/v1/collections", params={"status": "draft"}, headers=viewer.headers
        )
    ).json()["items"]
    assert [i["collection_id"] for i in drafts] == [draft["collection_id"]]
    assert drafts[0]["items_count"] == 2 and drafts[0]["version"] is None

    detail = (
        await svc.client.get(
            f"/admin/v1/collections/{draft['collection_id']}", headers=viewer.headers
        )
    ).json()
    assert detail["draft_revision"] == 2
    assert [i["custom"] for i in detail["items"]] == [{"emoji": "🎬🍿🎥🎭🌟🎻"}, {"emoji": "🎬🍿🎥🎭🌟🎻"}]
    by_title = (
        await svc.client.get(
            "/admin/v1/collections", params={"q": "опублик"}, headers=viewer.headers
        )
    ).json()["items"]
    assert [i["collection_id"] for i in by_title] == [published]


async def test_restriction_lifecycle(svc: Any) -> None:
    author = svc.user()
    admin = svc.admin("admin.access", "collections.restrict_author", "collections.view")
    url = f"/admin/v1/authors/{author.user_id}/restriction"
    restricted = await svc.client.post(url, json={"reason": "спам"}, headers=admin.mutate())
    assert restricted.json()["restricted"] is True and restricted.json()["reason"] == "спам"
    blocked = await svc.client.post(
        "/v1/collections", json={"domain": "film", "title": "x"}, headers=author.mutate()
    )
    assert_problem(blocked, 403, "collections.author_restricted")
    lifted = await svc.client.request(
        "DELETE", url, json={"reason": "исправился"}, headers=admin.mutate()
    )
    assert lifted.json()["restricted"] is False and lifted.json()["reason"] == "исправился"
    again = await svc.client.request(
        "DELETE", url, json={"reason": "повтор"}, headers=admin.mutate()
    )
    assert again.json()["restricted"] is False
    assert (await svc.create(author))["revision"] == 1
