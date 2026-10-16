"""Автор: создание, черновик с конфликтом устройств, публикация, удаление (K5, D1, D2, D8)."""

from __future__ import annotations

from typing import Any

import pytest
from collections_service.infrastructure.limits import RedisRateLimiter
from redis.asyncio import Redis

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component


def _body(items: list[Any], **extra: Any) -> dict[str, Any]:
    return {
        "title": "Кино · кадры",
        "default_mode_key": "film.collection.frame",
        "items": [{"media_item_id": str(i)} for i in items],
        **extra,
    }


async def test_create_returns_first_revision_with_normalized_title(svc: Any) -> None:
    author = svc.user()
    response = await svc.client.post(
        "/v1/collections",
        json={"domain": "film", "title": "  Кино\u200b   ·  кадры  "},
        headers=author.mutate(),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["revision"] == 1 and data["status"] == "draft" and data["domain"] == "film"
    assert data["body"]["title"] == "Кино · кадры"
    assert response.headers["cache-control"] == "no-store"

    got = await svc.client.get(
        f"/v1/collections/{data['collection_id']}/draft", headers=author.headers
    )
    assert got.json() == data


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("   ", "ugc.empty"),
        ("x" * 81, "ugc.too_long"),
        ("смотри guessverse.ru", "ugc.link_forbidden"),
        ("пиши @admin", "ugc.mention_forbidden"),
    ],
)
async def test_create_rejects_bad_title(svc: Any, title: str, reason: str) -> None:
    response = await svc.client.post(
        "/v1/collections", json={"domain": "film", "title": title}, headers=svc.user().mutate()
    )
    body = assert_problem(response, 422, "collections.title_invalid")
    assert body["details"]["reason"] == reason


async def test_create_respects_drafts_limit(service_factory: Any) -> None:
    svc = await service_factory(drafts_max=2)
    author = svc.user()
    for _ in range(2):
        await svc.create(author)
    response = await svc.client.post(
        "/v1/collections", json={"domain": "film", "title": "Третий"}, headers=author.mutate()
    )
    body = assert_problem(response, 409, "collections.drafts_limit")
    assert body["details"] == {"max": 2}


async def test_idempotent_create_returns_same_draft(svc: Any) -> None:
    author = svc.user()
    headers = author.mutate("same-key-0001")
    first = await svc.client.post(
        "/v1/collections", json={"domain": "film", "title": "A"}, headers=headers
    )
    again = await svc.client.post(
        "/v1/collections", json={"domain": "film", "title": "A"}, headers=headers
    )
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    other = await svc.client.post(
        "/v1/collections", json={"domain": "film", "title": "B"}, headers=headers
    )
    assert_problem(other, 409, "idempotency.key_reused")
    rows = await svc.sql("SELECT count(*) FROM collections WHERE author_id = :a", a=author.user_id)
    assert rows[0][0] == 1


async def test_draft_conflict_returns_current_draft(svc: Any) -> None:
    """D2: вторая вкладка с устаревшей ревизией получает 409 и актуальный черновик."""
    author = svc.user()
    draft = await svc.create(author)
    cid = draft["collection_id"]
    tab_a = await svc.client.put(
        f"/v1/collections/{cid}/draft",
        json={"base_revision": 1, "client_label": "ноутбук", "body": _body([], title="Вкладка A")},
        headers=author.mutate(),
    )
    assert tab_a.status_code == 200 and tab_a.json()["revision"] == 2
    tab_b = await svc.save(author, cid, _body([], title="Вкладка B"), base=1)
    body = assert_problem(tab_b, 409, "collections.draft_conflict")
    current = body["details"]["current"]
    assert current["revision"] == 2 and current["client_label"] == "ноутбук"
    assert current["body"]["title"] == "Вкладка A"
    assert tab_b.headers["cache-control"] == "no-store"


async def test_foreign_draft_is_not_found(svc: Any) -> None:
    author, stranger = svc.user(), svc.user()
    cid = (await svc.create(author))["collection_id"]
    for response in (
        await svc.client.get(f"/v1/collections/{cid}/draft", headers=stranger.headers),
        await svc.save(stranger, cid, _body([]), base=1),
        await svc.publish(stranger, cid, 1),
        await svc.client.delete(f"/v1/collections/{cid}", headers=stranger.mutate()),
        await svc.client.get(f"/v1/collections/{uuid7()}/draft", headers=author.headers),
        await svc.client.get(f"/v1/collections/{cid}", headers=author.headers),
    ):
        assert_problem(response, 404, "collections.not_found")


async def test_draft_limits_are_checked_on_save(service_factory: Any) -> None:
    svc = await service_factory(items_max=6, tags_max=1)
    author = svc.user()
    cid = (await svc.create(author))["collection_id"]
    response = await svc.save(
        author, cid, _body([uuid7() for _ in range(7)], tags=["horror", "comedy"]), base=1
    )
    body = assert_problem(response, 422, "collections.draft_invalid")
    codes = {e["code"] for e in body["details"]["errors"]}
    assert codes == {"collections.items_too_many", "collections.tags_too_many"}


async def test_publish_emits_valid_event_and_replay_returns_same(svc: Any) -> None:
    author = svc.user()
    items = await svc.add_items(6)
    draft = await svc.create(author)
    cid = draft["collection_id"]
    custom = [
        {"media_item_id": str(items[0]), "custom": {"emoji": "🚢🧊💔🌊🎻🥶"}},
        *({"media_item_id": str(i)} for i in items[1:]),
    ]
    saved = await svc.save(
        author,
        cid,
        {
            **_body([]),
            "items": custom,
            "tags": ["horror"],
            "cover": {"preset": "night"},
            "description": "Кадры\n\n\n\nиз фильмов",
        },
        base=1,
    )
    assert saved.status_code == 200, saved.text
    idem = "publish-key-0001"
    first = await svc.client.post(
        f"/v1/collections/{cid}/publish", json={"draft_revision": 2}, headers=author.mutate(idem)
    )
    assert first.status_code == 200, first.text
    detail = first.json()
    assert detail["version"] == 1 and detail["status"] == "published"
    assert detail["items_count"] == 6 and detail["items_visible"] is True
    assert detail["items"][0]["custom"] == {"emoji": "🚢🧊💔🌊🎻🥶"}
    assert detail["description"] == "Кадры\n\nиз фильмов"
    assert detail["me"] == {
        "liked": False,
        "favorite": False,
        "completed": False,
        "is_author": True,
    }
    assert detail["author"]["kind"] == "user" and detail["cover"] == {"preset": "night"}

    again = await svc.client.post(
        f"/v1/collections/{cid}/publish", json={"draft_revision": 2}, headers=author.mutate(idem)
    )
    assert again.json() == detail
    (event,) = await svc.events("collections.CollectionPublished", cid)
    assert_event_matches_schema(event)
    assert event.payload["version"] == 1 and event.payload["content_version"] == 1
    assert event.payload["items"][0]["custom"] == {"emoji": "🚢🧊💔🌊🎻🥶"}
    assert "custom" not in event.payload["items"][1]
    assert "title" not in event.payload

    # Черновика больше нет: GET отдаёт тело версии с revision 0 поверх content_version 1.
    view = (await svc.client.get(f"/v1/collections/{cid}/draft", headers=author.headers)).json()
    assert view["revision"] == 0 and view["base_version"] == 1 and view["status"] == "published"
    # Старая ревизия другого устройства не совпадёт (нет ABA): 2 ≠ 0.
    stale = await svc.save(author, cid, _body(items), base=2)
    assert_problem(stale, 409, "collections.draft_conflict")
    second = await svc.save(author, cid, _body(items[:5]), base=0)
    assert second.json()["revision"] == 3 and second.json()["base_version"] == 1
    republished = await svc.publish(author, cid, 3)
    assert republished.json()["version"] == 2 and republished.json()["items_count"] == 5
    events = await svc.events("collections.CollectionPublished", cid)
    assert [e.payload["version"] for e in events] == [1, 2]


async def test_publish_collects_all_errors(svc: Any) -> None:
    author = svc.user()
    film = await svc.add_items(3)
    music = await svc.add_item(domain="music")
    blocked = await svc.add_item()
    await svc.block_item(blocked)
    cid = (await svc.create(author))["collection_id"]
    items = [
        {"media_item_id": str(film[0]), "custom": {"emoji": "кот"}},
        {"media_item_id": str(film[0])},
        {"media_item_id": str(music)},
        {"media_item_id": str(blocked)},
        {"media_item_id": str(uuid7())},
    ]
    body = {
        "title": "мой сайт example.com",
        "default_mode_key": "music.collection.frame",
        "tags": ["rock", "nope"],
        "cover": {"preset": "nope"},
        "items": items,
    }
    saved = await svc.save(author, cid, body, base=1)
    response = await svc.publish(author, cid, saved.json()["revision"])
    problem = assert_problem(response, 422, "collections.publish_invalid")
    errors = {(e["path"], e["code"]) for e in problem["details"]["errors"]}
    assert errors == {
        ("title", "collections.title_invalid"),
        ("tags[0]", "collections.tag_unknown"),
        ("tags[1]", "collections.tag_unknown"),
        ("cover.preset", "collections.cover_unknown"),
        ("default_mode_key", "collections.mode_invalid"),
        ("items[0].custom.emoji", "collections.emoji_invalid"),
        ("items[1]", "collections.item_duplicate"),
        ("items[2]", "collections.item_domain_mismatch"),
        ("items[3]", "collections.item_unavailable"),
        ("items[4]", "collections.item_unavailable"),
    }
    assert await svc.events("collections.CollectionPublished", cid) == []


async def test_publish_with_stale_revision_conflicts(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.create(author))["collection_id"]
    await svc.save(author, cid, _body(await svc.add_items(5)), base=1)
    body = assert_problem(await svc.publish(author, cid, 1), 409, "collections.draft_conflict")
    assert body["details"]["current"]["revision"] == 2


async def test_publish_limit_is_enforced(service_factory: Any) -> None:
    svc = await service_factory(publish_per_day=1)
    author = svc.user()
    await svc.published(author)
    items = await svc.add_items(5)
    cid = (await svc.create(author, title="Второй"))["collection_id"]
    saved = await svc.save(author, cid, _body(items, title="Второй"), base=1)
    response = await svc.publish(author, cid, saved.json()["revision"])
    body = assert_problem(response, 429, "collections.publish_limit")
    assert body["details"]["max"] == 1 and int(response.headers["retry-after"]) >= 1


async def test_publish_fails_closed_without_redis(service_factory: Any, svc: Any) -> None:
    """§12 п. 4: недоступный лимитер публикаций — 503, черновик не публикуется."""
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    offline = await service_factory(RedisRateLimiter(broken, svc.clock))
    author = offline.user()
    items = await offline.add_items(5)
    cid = (await offline.create(author))["collection_id"]
    saved = await offline.save(author, cid, _body(items), base=1)
    response = await offline.publish(author, cid, saved.json()["revision"])
    assert_problem(response, 503, "collections.limiter_unavailable")
    # Лайки и избранное при этом fail-open: проходят.
    published = await svc.published(svc.user())
    fan = offline.user()
    like = await offline.client.put(
        f"/v1/collections/{published['collection_id']}/like", headers=fan.mutate()
    )
    assert like.status_code == 200, like.text
    await broken.aclose()


@pytest.mark.parametrize("how", ["sanction", "moderator"])
async def test_restricted_author_cannot_create_edit_or_publish(svc: Any, how: str) -> None:
    author = svc.user()
    cid = (await svc.create(author))["collection_id"]
    if how == "sanction":
        token = svc.issuer.issue(user_id=author.user_id, sanctions=int(Sanction.CUSTOM_CONTENT))
        author.headers = {"Authorization": f"Bearer {token}"}
    else:
        admin = svc.admin()
        restricted = await svc.client.post(
            f"/admin/v1/authors/{author.user_id}/restriction",
            json={"reason": "спам"},
            headers=admin.mutate(),
        )
        assert restricted.json()["restricted"] is True
    for response in (
        await svc.client.post(
            "/v1/collections", json={"domain": "film", "title": "x"}, headers=author.mutate()
        ),
        await svc.save(author, cid, _body([]), base=1),
        await svc.publish(author, cid, 1),
    ):
        assert_problem(response, 403, "collections.author_restricted")
    # Удалить своё можно.
    assert (
        await svc.client.delete(f"/v1/collections/{cid}", headers=author.mutate())
    ).status_code == 204


async def test_delete_published_emits_event_once(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.published(author))["collection_id"]
    for _ in range(2):
        response = await svc.client.delete(f"/v1/collections/{cid}", headers=author.mutate())
        assert response.status_code == 204
    (event,) = await svc.events("collections.CollectionDeleted", cid)
    assert_event_matches_schema(event)
    assert event.payload["version"] == 2
    assert_problem(await svc.client.get(f"/v1/collections/{cid}"), 404, "collections.not_found")
    assert_problem(await svc.save(author, cid, _body([]), base=0), 409, "collections.deleted")
    assert_problem(await svc.publish(author, cid, 1), 409, "collections.deleted")


async def test_delete_draft_emits_nothing(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.create(author))["collection_id"]
    assert (
        await svc.client.delete(f"/v1/collections/{cid}", headers=author.mutate())
    ).status_code == 204
    assert await svc.outbox() == []
    mine = (await svc.client.get("/v1/me/collections", headers=author.headers)).json()
    assert mine["items"] == []


async def test_my_collections_lists_drafts_and_published_with_cursor(svc: Any) -> None:
    author = svc.user()
    published = await svc.published(author, title="Опубликованный")
    drafts = [await svc.create(author, title=f"Черновик {n}") for n in range(3)]
    svc.clock.advance(seconds=1)
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (
            await svc.client.get("/v1/me/collections", params=params, headers=author.headers)
        ).json()
        seen += [i["collection_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(
        [published["collection_id"], *(d["collection_id"] for d in drafts)]
    )
    assert len(seen) == len(set(seen))
    only = (
        await svc.client.get(
            "/v1/me/collections", params={"status": "published"}, headers=author.headers
        )
    ).json()["items"]
    assert [i["collection_id"] for i in only] == [published["collection_id"]]
    assert only[0]["version"] == 1 and only[0]["has_draft"] is False
    assert only[0]["hidden_by_moderator"] is False
    draft_row = next(i for i in seen if i == drafts[0]["collection_id"])
    assert draft_row
