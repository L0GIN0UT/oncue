"""Медиатека через HTTP (план этапа 3b §6, L3/L5; §12 п. 1, 2, 4, 6, 7).

Сервис целиком на реальных Postgres и Redis; единицы каталога приходят событиями catalog.
"""

from __future__ import annotations

from typing import Any

import pytest
from library_service.settings import LibraryLimits

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope
from gv_testkit import assert_event_matches_schema, assert_problem
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.component

SYSTEM_REFS = ["system:watch_later", "system:watched", "system:favorite"]


def _library_events(events: list[EventEnvelope]) -> list[EventEnvelope]:
    return [e for e in events if e.producer == "library"]


async def test_shelves_list_system_first_even_when_empty(library: Any) -> None:
    _, me = library.user()
    response = await library.client.get("/v1/shelves", headers=me)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    shelves = response.json()
    assert [s["ref"] for s in shelves] == SYSTEM_REFS
    watch_later = shelves[0]
    assert watch_later == {
        "ref": "system:watch_later",
        "shelf_id": None,
        "kind": "system",
        "key": "watch_later",
        "title": None,
        "title_key": "library.shelf.watch_later",
        "domains": ["film"],
        "exclusive_with": ["watched"],
        "items_count": 0,
        "updated_at": None,
    }


async def test_save_from_reveal_creates_shelf_lazily_and_publishes_valid_event(
    library: Any,
) -> None:
    user_id, me = library.user()
    item = await library.add_item(title="Интерстеллар")

    response = await library.save(me, "system:watch_later", item, "reveal")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "media_item_id": str(item),
        "changed": True,
        "shelves": ["system:watch_later"],
        "removed_from": [],
    }

    events = _library_events(await library.outbox())
    assert [e.event_type for e in events] == ["library.ItemSaved"]
    saved = events[0]
    assert_event_matches_schema(saved)
    assert saved.topic == load_event_schema("library.ItemSaved")["x-topic"]
    assert saved.aggregate_id == str(user_id)
    assert saved.payload["shelf_kind"] == "system"
    assert saved.payload["shelf_key"] == "watch_later"
    assert saved.payload["source"] == "reveal"
    assert saved.payload["domain"] == "film"
    assert saved.payload["version"] == 1

    shelves = (await library.client.get("/v1/shelves", headers=me)).json()
    assert shelves[0]["shelf_id"] == saved.payload["shelf_id"]
    assert shelves[0]["items_count"] == 1


async def test_repeat_save_is_noop_without_event(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    assert (await library.save(me, "system:favorite", item, "card")).json()["changed"] is True
    again = await library.save(me, "system:favorite", item, "results")
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert len(_library_events(await library.outbox())) == 1


async def test_idempotency_key_replays_response_and_rejects_other_body(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    first = await library.save(me, "system:watched", item, "results", idem="same-key-0001")
    replay = await library.save(me, "system:watched", item, "results", idem="same-key-0001")
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["changed"] is True  # сохранённый ответ, а не повторное выполнение
    other = await library.save(me, "system:watched", item, "card", idem="same-key-0001")
    assert_problem(other, 409, "idempotency.key_reused")
    missing = await library.client.put(
        f"/v1/shelves/system:watched/items/{item}", json={"source": "card"}, headers=me
    )
    assert_problem(missing, 422, "idempotency.key_required")


async def test_watched_removes_from_watch_later_by_exclusive_with(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    await library.save(me, "system:watch_later", item, "reveal")
    response = await library.save(me, "system:watched", item, "results")
    assert response.json() == {
        "media_item_id": str(item),
        "changed": True,
        "shelves": ["system:watched"],
        "removed_from": ["system:watch_later"],
    }
    events = _library_events(await library.outbox())
    assert [(e.event_type, e.payload["shelf_key"], e.payload["source"]) for e in events] == [
        ("library.ItemSaved", "watch_later", "reveal"),
        ("library.ItemSaved", "watched", "results"),
        ("library.ItemRemoved", "watch_later", "exclusive"),
    ]
    assert [e.payload["version"] for e in events] == [1, 2, 3]
    for event in events:
        assert_event_matches_schema(event)

    item_shelves = await library.client.get(f"/v1/items/{item}/shelves", headers=me)
    assert item_shelves.json() == {"media_item_id": str(item), "shelves": ["system:watched"]}


async def test_unknown_or_blocked_item_is_unavailable(library: Any) -> None:
    _, me = library.user()
    unknown = await library.save(me, "system:favorite", uuid7())
    assert_problem(unknown, 422, "library.item_unavailable")

    item = await library.add_item()
    await library.set_blocked(item, blocked=True, version=2)
    blocked = await library.save(me, "system:favorite", item)
    assert_problem(blocked, 422, "library.item_unavailable")
    # Неудачное сохранение не создаёт системную полку и не пишет событий.
    shelves = (await library.client.get("/v1/shelves", headers=me)).json()
    assert shelves[2]["shelf_id"] is None
    assert _library_events(await library.outbox()) == []


async def test_domain_mismatch(library: Any) -> None:
    _, me = library.user()
    track = await library.add_item(domain="music", persons=[("Земфира", "artist")])
    body = assert_problem(
        await library.save(me, "system:watch_later", track), 422, "library.domain_mismatch"
    )
    assert body["details"] == {"domain": "music", "allowed": ["film"]}
    assert (await library.save(me, "system:favorite", track)).status_code == 200


async def test_blocked_item_stays_on_shelf_as_unavailable(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item(
        title="Начало",
        year=2010,
        persons=[("Ханс Циммер", "composer"), ("Кристофер Нолан", "director")],
    )
    await library.save(me, "system:favorite", item, "card")
    await library.set_blocked(item, blocked=True, version=2)

    page = (await library.client.get("/v1/shelves/system:favorite/items", headers=me)).json()
    assert page["shelf"]["ref"] == "system:favorite"
    assert page["items"] == [
        {
            "media_item_id": str(item),
            "domain": "film",
            "kind": "film",
            "title": "Начало",
            "year": 2010,
            "persons": ["Кристофер Нолан"],
            "status": "unavailable",
            "source": "card",
            "added_at": page["items"][0]["added_at"],
        }
    ]
    assert page["next_cursor"] is None

    await library.set_blocked(item, blocked=False, version=3)
    page = (await library.client.get("/v1/shelves/system:favorite/items", headers=me)).json()
    assert page["items"][0]["status"] == "available"
    # Удаление недоступной единицы работает (проекция не нужна).
    await library.set_blocked(item, blocked=True, version=4)
    removed = await library.remove(me, "system:favorite", item, "library")
    assert removed.json()["changed"] is True


async def test_shelf_page_keyset_cursor_newest_first(library: Any) -> None:
    from datetime import timedelta

    _, me = library.user()
    items = []
    for _ in range(5):
        items.append(await library.add_item())
        await library.save(me, "system:favorite", items[-1])
        library.clock.advance(timedelta(seconds=1))
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (
            await library.client.get("/v1/shelves/system:favorite/items", params=params, headers=me)
        ).json()
        seen += [i["media_item_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [str(i) for i in reversed(items)]
    bad = await library.client.get(
        "/v1/shelves/system:favorite/items", params={"cursor": "bm90LWpzb24"}, headers=me
    )
    assert_problem(bad, 422, "pagination.bad_cursor")


async def test_remove_is_idempotent_and_noop_on_uncreated_shelf(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    noop = await library.remove(me, "system:watched", item)
    assert noop.status_code == 200
    assert noop.json() == {
        "media_item_id": str(item),
        "changed": False,
        "shelves": [],
        "removed_from": [],
    }
    await library.save(me, "system:watched", item)
    first = await library.remove(me, "system:watched", item, "card")
    assert first.json()["changed"] is True
    again = await library.remove(me, "system:watched", item, "card")
    assert again.json()["changed"] is False
    events = _library_events(await library.outbox())
    assert [e.event_type for e in events] == ["library.ItemSaved", "library.ItemRemoved"]
    assert events[1].payload["source"] == "card"
    assert_event_matches_schema(events[1])


async def test_custom_shelf_lifecycle(library: Any) -> None:
    _, me = library.user()
    created = await library.client.post(
        "/v1/shelves", json={"title": "  Ужасы\u200b 80-х  "}, headers=me | library.key()
    )
    assert created.status_code == 201, created.text
    shelf = created.json()
    assert shelf["kind"] == "custom"
    assert shelf["title"] == "Ужасы 80-х"  # нормализовано gv_text.ugc
    assert shelf["ref"] == shelf["shelf_id"]
    assert shelf["domains"] == ["music", "film"]
    ref = shelf["ref"]

    film = await library.add_item()
    track = await library.add_item(domain="music")
    for item in (film, track):
        assert (await library.save(me, ref, item, "collection")).status_code == 200

    renamed = await library.client.patch(
        f"/v1/shelves/{ref}", json={"title": "Ужасы 70-х"}, headers=me | library.key()
    )
    assert renamed.json()["title"] == "Ужасы 70-х"
    assert renamed.json()["items_count"] == 2

    shelves = (await library.client.get("/v1/shelves", headers=me)).json()
    assert [s["ref"] for s in shelves] == [*SYSTEM_REFS, ref]

    deleted = await library.client.delete(f"/v1/shelves/{ref}", headers=me | library.key())
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    gone = await library.client.get(f"/v1/shelves/{ref}/items", headers=me)
    assert_problem(gone, 404, "library.shelf_not_found")

    events = _library_events(await library.outbox())
    removed = [e for e in events if e.event_type == "library.ItemRemoved"]
    assert {e.payload["source"] for e in removed} == {"shelf_deleted"}
    assert {e.payload["media_item_id"] for e in removed} == {str(film), str(track)}
    assert all(
        e.payload["shelf_kind"] == "custom" and e.payload["shelf_key"] is None for e in removed
    )
    for event in events:
        assert_event_matches_schema(event)
    assert [e.payload["version"] for e in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("\u200b \u2066", "ugc.empty"),
        ("x" * 61, "ugc.too_long"),
        ("смотри на example.com", "ugc.link_forbidden"),
        ("для @друга", "ugc.mention_forbidden"),
    ],
)
async def test_custom_shelf_title_goes_through_ugc(library: Any, title: str, reason: str) -> None:
    _, me = library.user()
    response = await library.client.post(
        "/v1/shelves", json={"title": title}, headers=me | library.key()
    )
    body = assert_problem(response, 422, "library.title_invalid")
    assert body["details"]["reason"] == reason


@pytest.mark.parametrize(
    "library_limits", [LibraryLimits(custom_shelves_max=2, items_per_shelf_max=1)]
)
async def test_limits_shelves_and_items(library: Any) -> None:
    _, me = library.user()
    refs = []
    for i in range(2):
        r = await library.client.post(
            "/v1/shelves", json={"title": f"Полка {i}"}, headers=me | library.key()
        )
        refs.append(r.json()["ref"])
    over = await library.client.post(
        "/v1/shelves", json={"title": "Третья"}, headers=me | library.key()
    )
    assert assert_problem(over, 409, "library.shelves_limit")["details"] == {"max": 2}

    a, b = await library.add_item(), await library.add_item()
    assert (await library.save(me, refs[0], a)).status_code == 200
    full = await library.save(me, refs[0], b)
    assert assert_problem(full, 409, "library.shelf_full")["details"] == {"max": 1}
    # Повтор уже лежащей единицы на полной полке — не ошибка.
    assert (await library.save(me, refs[0], a)).json()["changed"] is False


@pytest.mark.parametrize("library_limits", [LibraryLimits(saves_per_min=2)])
async def test_saves_are_rate_limited(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    for _ in range(2):
        assert (await library.save(me, "system:favorite", item)).status_code == 200
    limited = await library.save(me, "system:favorite", item)
    body = assert_problem(limited, 429, "library.rate_limited")
    assert int(limited.headers["retry-after"]) >= 1
    assert body["details"]["retry_after"] >= 1
    # Лимит — на пользователя: другой пользователь сохраняет.
    _, other = library.user()
    assert (await library.save(other, "system:favorite", item)).status_code == 200


async def test_library_is_private_foreign_shelf_is_404(library: Any) -> None:
    _, alice = library.user()
    _, bob = library.user()
    shelf = (
        await library.client.post(
            "/v1/shelves", json={"title": "Личное"}, headers=alice | library.key()
        )
    ).json()
    item = await library.add_item()
    await library.save(alice, shelf["ref"], item)
    await library.save(alice, "system:favorite", item)

    for method, path, body in [
        ("GET", f"/v1/shelves/{shelf['ref']}/items", None),
        ("PUT", f"/v1/shelves/{shelf['ref']}/items/{item}", {"source": "card"}),
        ("DELETE", f"/v1/shelves/{shelf['ref']}/items/{item}", None),
        ("PATCH", f"/v1/shelves/{shelf['ref']}", {"title": "Моё"}),
        ("DELETE", f"/v1/shelves/{shelf['ref']}", None),
    ]:
        response = await library.client.request(
            method, path, json=body, headers=bob | library.key()
        )
        assert_problem(response, 404, "library.shelf_not_found")

    # У Боба своя (пустая) медиатека: чужие полки и единицы не видны.
    assert [s["ref"] for s in (await library.client.get("/v1/shelves", headers=bob)).json()] == (
        SYSTEM_REFS
    )
    bob_items = await library.client.get(f"/v1/items/{item}/shelves", headers=bob)
    assert bob_items.json()["shelves"] == []
    favorite = await library.client.get("/v1/shelves/system:favorite/items", headers=bob)
    assert favorite.json()["items"] == []


async def test_system_shelf_id_is_not_a_custom_shelf(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    await library.save(me, "system:favorite", item)
    shelf_id = (await library.client.get("/v1/shelves", headers=me)).json()[2]["shelf_id"]
    rename = await library.client.patch(
        f"/v1/shelves/{shelf_id}", json={"title": "Моё"}, headers=me | library.key()
    )
    assert_problem(rename, 404, "library.shelf_not_found")
    by_id = await library.client.get(f"/v1/shelves/{shelf_id}/items", headers=me)
    assert_problem(by_id, 404, "library.shelf_not_found")
    unknown = await library.client.get("/v1/shelves/system:wishlist/items", headers=me)
    assert_problem(unknown, 404, "library.shelf_not_found")


ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/v1/shelves", None),
    ("POST", "/v1/shelves", {"title": "Ужасы 80-х"}),
    ("PATCH", f"/v1/shelves/{uuid7()}", {"title": "Ужасы 70-х"}),
    ("DELETE", f"/v1/shelves/{uuid7()}", None),
    ("GET", "/v1/shelves/system:watch_later/items", None),
    ("PUT", f"/v1/shelves/system:watch_later/items/{uuid7()}", {"source": "reveal"}),
    ("DELETE", f"/v1/shelves/system:watched/items/{uuid7()}?source=card", None),
    ("GET", f"/v1/items/{uuid7()}/shelves", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_library_is_for_users_only(
    library: Any, method: str, path: str, body: dict[str, object] | None
) -> None:
    """D7, D11: медиатека приватна и только у пользователей."""
    response = await library.client.request(method, path, json=body, headers=library.key())
    assert_problem(response, 401, "auth.unauthenticated")
    guest = {"Authorization": f"Bearer {library.issuer.issue_guest()}"} | library.key()
    response = await library.client.request(method, path, json=body, headers=guest)
    assert_problem(response, 403, "auth.guest_forbidden")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/shelves/system:Watch-Later/items", None),
        ("GET", "/v1/shelves/123/items", None),
        ("GET", "/v1/shelves/system:/items", None),
        ("GET", "/v1/shelves/0192A3B4-C5D6-7E8F-9A0B-000000000D01/items", None),
        ("PUT", f"/v1/shelves/system:watched/items/{uuid7()}", {"source": "elsewhere"}),
        (
            "PUT",
            f"/v1/shelves/system:watched/items/{uuid7()}",
            {"source": "reveal", "user_id": "x"},
        ),
        ("DELETE", f"/v1/shelves/system:watched/items/{uuid7()}?source=exclusive", None),
        ("POST", "/v1/shelves", {"title": ""}),
        ("POST", "/v1/shelves", {"title": "Мои", "kind": "system"}),
        ("PATCH", "/v1/shelves/system:watched", {"title": "Мои"}),
    ],
)
async def test_shelf_ref_and_bodies_are_strict(
    library: Any, method: str, path: str, body: dict[str, object] | None
) -> None:
    """``shelf_ref`` разбирается строго, лишние поля запрещены (mass assignment)."""
    _, me = library.user()
    response = await library.client.request(method, path, json=body, headers=me | library.key())
    assert response.status_code == 422, response.text


async def test_cookie_mutation_requires_csrf_and_origin(library: Any) -> None:
    user_id, _ = library.user()
    item = await library.add_item()
    token = library.issuer.issue(user_id=user_id)
    path = f"/v1/shelves/system:favorite/items/{item}"
    library.client.cookies.set("gv_access", token)
    try:
        no_csrf = await library.client.put(path, json={"source": "card"}, headers=library.key())
        assert_problem(no_csrf, 403, "auth.csrf_failed")
        library.client.cookies.set("gv_csrf", "csrf-token-value")
        csrf = {"X-CSRF-Token": "csrf-token-value"}
        foreign = await library.client.put(
            path,
            json={"source": "card"},
            headers=csrf | {"Origin": "https://evil.test"} | library.key(),
        )
        assert_problem(foreign, 403, "auth.csrf_failed")
        ok = await library.client.put(
            path,
            json={"source": "card"},
            headers=csrf | {"Origin": "https://oncue.test"} | library.key(),
        )
        assert ok.status_code == 200, ok.text
        # Чтение по cookie CSRF не требует.
        read = await library.client.get("/v1/shelves")
        assert read.status_code == 200
    finally:
        library.client.cookies.clear()


async def test_user_deleted_purges_library_in_batches(library: Any) -> None:
    from gv_testkit import load_example

    user_id, me = library.user()
    _, other = library.user()
    items = [await library.add_item() for _ in range(5)]
    shelf = (
        await library.client.post("/v1/shelves", json={"title": "Моё"}, headers=me | library.key())
    ).json()
    for item in items:
        await library.save(me, "system:favorite", item)
        await library.save(me, shelf["ref"], item)
    await library.save(other, "system:favorite", items[0])

    deleted = load_example("identity.UserDeleted")
    deleted = deleted.model_copy(
        update={
            "aggregate_id": str(user_id),
            "payload": {**deleted.payload, "user_id": str(user_id)},
        }
    )
    await library.deliver(deleted)
    # Первая порция (purge_batch=2) — в consumer; остальное дочищает задача relay.
    assert (
        await library.client.get(f"/v1/items/{items[0]}/shelves", headers=me)
    ).status_code == 200
    result = await library.run_job("library.users.purge")
    assert result.value == "ok"

    shelves = (await library.client.get("/v1/shelves", headers=me)).json()
    assert [s["ref"] for s in shelves] == SYSTEM_REFS
    assert all(s["shelf_id"] is None for s in shelves)
    # Повтор события и задачи — no-op; чужая медиатека не тронута.
    await library.deliver(deleted.model_copy(update={"event_id": uuid7()}))
    assert (await library.run_job("library.users.purge")).value == "ok"
    theirs = await library.client.get(f"/v1/items/{items[0]}/shelves", headers=other)
    assert theirs.json()["shelves"] == ["system:favorite"]
    assert (
        await library.client.get(f"/v1/shelves/{shelf['ref']}/items", headers=me)
    ).status_code == 404


async def test_items_shelves_batch_returns_own_placements(library: Any) -> None:
    """Пакет «в каких полках единицы»: только свои полки, дубли схлопнуты, пустые — с []."""
    _, me = library.user()
    _, other = library.user()
    first, second, third = [await library.add_item() for _ in range(3)]
    await library.save(me, "system:favorite", first)
    await library.save(me, "system:watched", first)
    await library.save(me, "system:favorite", second)
    await library.save(other, "system:watched", third)

    response = await library.client.get(
        "/v1/items/shelves",
        params=[
            ("ids", str(first)),
            ("ids", str(second)),
            ("ids", str(third)),
            ("ids", str(first)),
        ],
        headers=me,
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    by_id = {i["media_item_id"]: i["shelves"] for i in response.json()["items"]}
    assert len(response.json()["items"]) == 3
    assert by_id[str(first)] == ["system:watched", "system:favorite"]  # порядок показа
    assert by_id[str(second)] == ["system:favorite"]
    assert by_id[str(third)] == []  # чужая полка не видна


async def test_items_shelves_batch_matches_single_endpoint(library: Any) -> None:
    _, me = library.user()
    item = await library.add_item()
    await library.save(me, "system:favorite", item)
    single = (await library.client.get(f"/v1/items/{item}/shelves", headers=me)).json()
    batch = await library.client.get("/v1/items/shelves", params={"ids": str(item)}, headers=me)
    assert batch.json()["items"] == [single]


async def test_items_shelves_batch_limits(library: Any) -> None:
    _, me = library.user()
    too_many = [("ids", str(uuid7())) for _ in range(51)]
    assert_problem(
        await library.client.get("/v1/items/shelves", params=too_many, headers=me),
        422,
        "library.batch_too_large",
    )
    empty = await library.client.get("/v1/items/shelves", headers=me)
    assert empty.status_code == 422
    bad = await library.client.get("/v1/items/shelves", params={"ids": "nope"}, headers=me)
    assert bad.status_code == 422


@pytest.mark.parametrize("auth", ["anon", "guest"])
async def test_items_shelves_batch_requires_user(library: Any, auth: str) -> None:
    headers = {} if auth == "anon" else {"Authorization": f"Bearer {library.issuer.issue_guest()}"}
    response = await library.client.get(
        "/v1/items/shelves", params={"ids": str(uuid7())}, headers=headers
    )
    if auth == "anon":
        assert_problem(response, 401, "auth.unauthenticated")
    else:
        assert_problem(response, 403, "auth.guest_forbidden")
