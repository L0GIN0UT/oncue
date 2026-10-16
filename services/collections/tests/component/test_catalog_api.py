"""Каталог через API (K6, D10): поиск, фильтры, сортировки, курсор, кэширование."""

from __future__ import annotations

from typing import Any

import pytest

from gv_testkit import assert_problem

pytestmark = pytest.mark.component


async def _ids(svc: Any, **params: Any) -> list[str]:
    response = await svc.client.get("/v1/collections", params=params)
    assert response.status_code == 200, response.text
    return [i["collection_id"] for i in response.json()["items"]]


async def test_search_by_title_description_and_tag_words(svc: Any) -> None:
    author = svc.user()
    frames = (await svc.published(author, title="Кино · кадры"))["collection_id"]
    horror = (await svc.published(author, title="Страшное", tags=["horror"], description="Ночь"))[
        "collection_id"
    ]
    music = (await svc.published(author, title="Рок-хиты", domain="music"))["collection_id"]

    assert await _ids(svc, q="кадры") == [frames]
    assert await _ids(svc, q="кадр") == [frames]  # стемминг russian
    assert await _ids(svc, q="ужасы") == [horror]  # слово тега из словаря
    assert await _ids(svc, q="ночь") == [horror]
    assert await _ids(svc, q="хиты") == [music]
    assert set(await _ids(svc, domain="film")) == {frames, horror}
    assert await _ids(svc, domain="music", mode="music.collection.frame") == [music]
    assert await _ids(svc, mode="film.collection.emoji") == []


async def test_search_does_not_match_item_titles(svc: Any) -> None:
    """D6: поиск по единицам выдал бы состав сборника."""
    item = await svc.add_item(title="Интерстеллар")
    others = await svc.add_items(4)
    await svc.published(svc.user(), items=[item, *others], title="Космос")
    assert await _ids(svc, q="интерстеллар") == []


async def test_catalog_cache_headers(svc: Any) -> None:
    await svc.published(svc.user())
    plain = await svc.client.get("/v1/collections")
    assert plain.headers["cache-control"] == "public, max-age=30"
    searched = await svc.client.get("/v1/collections", params={"q": "кино"})
    assert searched.headers["cache-control"] == "no-store"


async def test_sort_new_and_likes_with_cursor(svc: Any) -> None:
    created = []
    for _ in range(5):
        svc.clock.advance(seconds=10)
        created.append((await svc.published(svc.user()))["collection_id"])
    fans = [svc.user() for _ in range(3)]
    for n, fan in enumerate(fans):
        for cid in created[: n + 1]:
            await svc.client.put(f"/v1/collections/{cid}/like", headers=fan.mutate())

    newest = await _ids(svc, sort="new")
    assert newest == list(reversed(created))
    liked = await _ids(svc, sort="likes")
    assert liked[:3] == created[:3]

    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"sort": "new", "limit": 2} | (
            {"cursor": cursor} if cursor else {}
        )
        page = (await svc.client.get("/v1/collections", params=params)).json()
        seen += [i["collection_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == newest


async def test_cursor_of_other_sort_is_rejected(svc: Any) -> None:
    for _ in range(2):
        await svc.published(svc.user())
    page = (await svc.client.get("/v1/collections", params={"sort": "new", "limit": 1})).json()
    response = await svc.client.get(
        "/v1/collections", params={"sort": "likes", "cursor": page["next_cursor"]}
    )
    assert_problem(response, 422, "pagination.bad_cursor")
    assert_problem(
        await svc.client.get("/v1/collections", params={"cursor": "bm90LWpzb24"}),
        422,
        "pagination.bad_cursor",
    )


async def test_curated_filter_and_featured_sort(svc: Any) -> None:
    ugc = (await svc.published(svc.user()))["collection_id"]
    curator = svc.admin()
    items = await svc.add_items(5)
    curated = await svc.client.post(
        "/admin/v1/collections",
        json={
            "domain": "film",
            "title": "Лучшее 90-х",
            "default_mode_key": "film.collection.frame",
            "items": [{"media_item_id": str(i)} for i in items],
            "reason": "кураторский",
        },
        headers=curator.mutate(),
    )
    assert curated.status_code == 201, curated.text
    cur = curated.json()["collection_id"]
    assert await _ids(svc, curated="true") == [cur]
    assert await _ids(svc, curated="false") == [ugc]
    assert await _ids(svc, sort="featured") == []
    for cid, rank in ((ugc, 2), (cur, 1)):
        response = await svc.client.put(
            f"/admin/v1/collections/{cid}/featured",
            json={"featured": True, "rank": rank, "reason": "подборка"},
            headers=curator.mutate(),
        )
        assert response.json()["featured_rank"] == rank
    assert await _ids(svc, sort="featured") == [cur, ugc]
    card = (await svc.client.get("/v1/collections", params={"sort": "featured"})).json()["items"][0]
    assert card["featured"] is True and card["author"] == {
        "kind": "curator",
        "user_id": None,
        "nickname": None,
    }


async def test_difficulty_filter_and_sort(service_factory: Any) -> None:
    svc = await service_factory()
    hard = (await svc.published(svc.user()))["collection_id"]
    easy = (await svc.published(svc.user()))["collection_id"]
    unrated = (await svc.published(svc.user()))["collection_id"]
    for _ in range(10):
        await svc.finish_game(svc.user(), hard, score=0, max_score=24)
        await svc.finish_game(svc.user(), easy, score=24, max_score=24)
    assert await _ids(svc, difficulty="hard") == [hard]
    assert await _ids(svc, difficulty="easy") == [easy]
    assert await _ids(svc, difficulty="medium") == []
    assert await _ids(svc, sort="difficulty") == [hard, easy, unrated]
    card = (await svc.client.get(f"/v1/collections/{hard}")).json()
    assert card["difficulty"] == "hard" and card["plays_count"] == 10
