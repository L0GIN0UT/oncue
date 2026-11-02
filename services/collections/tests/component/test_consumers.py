"""Consumers (K7) и периодические задачи (K8): идемпотентность по inbox и ``version``."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_service import JobResult
from gv_testkit import (
    assert_event_matches_schema,
    assert_idempotency_purge,
    assert_problem,
    load_example,
)

pytestmark = pytest.mark.component


async def _profile(svc: Any, user_id: UUID, nickname: str, version: int, kind: str) -> Outcome:
    base = load_example(f"identity.{kind}")
    payload = {**base.payload, "user_id": str(user_id), "nickname": nickname, "version": version}
    return await svc.deliver(
        base.model_copy(update={"aggregate_id": str(user_id), "payload": payload})
    )


async def _user_deleted(svc: Any, user_id: UUID, version: int = 9) -> Outcome:
    base = load_example("identity.UserDeleted")
    payload = {**base.payload, "user_id": str(user_id), "version": version}
    return await svc.deliver(
        base.model_copy(update={"aggregate_id": str(user_id), "payload": payload})
    )


# ---------------------------------------------------------------- catalog
async def test_blocked_item_is_removed_with_new_version(svc: Any) -> None:
    author, player = svc.user(), svc.user()
    items = await svc.add_items(5)
    cid = (await svc.published(author, items=items))["collection_id"]
    await svc.finish_game(player, cid, version=1)
    hidden = (await svc.published(author, items=items))["collection_id"]
    await svc.client.post(
        f"/admin/v1/collections/{hidden}/hide",
        json={"reason": "спам"},
        headers=svc.admin().mutate(),
    )
    assert await svc.block_item(items[2]) is Outcome.PROCESSED
    (event,) = await svc.events("collections.CollectionUpdated", cid)
    hidden_events = await svc.events("collections.CollectionUpdated", hidden)
    assert [e.payload["status"] for e in hidden_events] == ["hidden", "hidden"]
    for updated in (event, hidden_events[-1]):
        assert_event_matches_schema(updated)
        assert str(items[2]) not in {i["media_item_id"] for i in updated.payload["items"]}
        assert updated.payload["content_version"] == 2
    card = (await svc.client.get(f"/v1/collections/{cid}", headers=author.headers)).json()
    assert card["version"] == 2 and card["items_count"] == 4
    # Повтор и устаревшая версия — без новой версии.
    assert await svc.block_item(items[2]) is Outcome.PROCESSED
    assert await svc.block_item(items[2], version=1) is Outcome.PROCESSED
    assert len(await svc.events("collections.CollectionUpdated", cid)) == 1
    # Публикация черновика с заблокированной единицей не пройдёт.
    saved = await svc.save(
        author,
        cid,
        {
            "title": "Кино",
            "default_mode_key": "film.collection.frame",
            "items": [{"media_item_id": str(i)} for i in items],
        },
        base=0,
    )
    response = await svc.publish(author, cid, saved.json()["revision"])
    problem = assert_problem(response, 422, "collections.publish_invalid")
    assert problem["details"]["errors"] == [
        {
            "path": "items[2]",
            "code": "collections.item_unavailable",
            "message": "Единица №3 недоступна: её нет в каталоге или она скрыта",
        }
    ]


async def test_blocked_sweep_handles_remainder(service_factory: Any) -> None:
    """Порции: consumer убирает первую, задача ``collections.blocked.sweep`` — остальное."""
    svc = await service_factory()
    items = await svc.add_items(5)
    ids = [(await svc.published(svc.user(), items=items))["collection_id"] for _ in range(3)]
    # Блокировка «мимо» consumer: только проекция (как если бы порция была 0).
    await svc.sql_exec(
        "UPDATE catalog_items_proj SET status = 'blocked', version = 2 WHERE media_item_id = :i",
        i=items[0],
    )
    assert await svc.run_job("collections.blocked.sweep") is JobResult.OK
    for cid in ids:
        (event,) = await svc.events("collections.CollectionUpdated", cid)
        assert len(event.payload["items"]) == 4
    assert await svc.run_job("collections.blocked.sweep") is JobResult.OK
    for cid in ids:
        assert len(await svc.events("collections.CollectionUpdated", cid)) == 1


async def test_catalog_updates_respect_version(svc: Any) -> None:
    item = await svc.add_item(title="Старое")
    base = load_example("catalog.MediaItemUpdated")
    for version, title in ((3, "Новое"), (2, "Устаревшее")):
        payload = {
            **base.payload,
            "media_item_id": str(item),
            "version": version,
            "title": title,
            "domain": "film",
            "status": "active",
        }
        event = base.model_copy(update={"payload": payload, "event_id": uuid7()})
        assert await svc.deliver(event) is Outcome.PROCESSED
    rows = await svc.sql(
        "SELECT title, version FROM catalog_items_proj WHERE media_item_id = :i", i=item
    )
    assert [tuple(r) for r in rows] == [("Новое", 3)]
    unblocked = load_example("catalog.MediaItemUnblocked")
    payload = {**unblocked.payload, "media_item_id": str(uuid7()), "version": 5}
    assert await svc.deliver(unblocked.model_copy(update={"payload": payload})) is Outcome.PROCESSED


async def test_bad_payload_goes_to_dlq(svc: Any) -> None:
    base = load_example("catalog.MediaItemBlocked")
    broken = base.model_copy(update={"payload": {"media_item_id": "nope"}})
    assert await svc.deliver(broken) is Outcome.DEAD


# ---------------------------------------------------------------- game
async def test_game_finished_counts_first_completion_of_users_only(svc: Any) -> None:
    author, player = svc.user(), svc.user()
    cid = (await svc.published(author))["collection_id"]
    assert await svc.finish_game(player, cid, score=12, max_score=24) is Outcome.PROCESSED
    assert await svc.finish_game(player, cid, score=24, max_score=24) is Outcome.PROCESSED
    await svc.finish_game(None, cid, guest=True)
    await svc.finish_game(author, cid)  # самоигра автора не считается
    await svc.finish_game(svc.user(), str(uuid7()))  # неизвестный сборник
    rows = await svc.sql(
        "SELECT plays_count, ratio_sum, ratio_count FROM collections WHERE id = :c", c=cid
    )
    assert tuple(rows[0]) == (1, 0.5, 1)
    completions = await svc.sql(
        "SELECT user_id, ratio FROM completions WHERE collection_id = :c", c=cid
    )
    assert [(r.user_id, r.ratio) for r in completions] == [(player.user_id, 0.5)]
    card = (await svc.client.get(f"/v1/collections/{cid}", headers=player.headers)).json()
    assert card["me"]["completed"] is True and card["plays_count"] == 1
    assert card["difficulty"] is None  # меньше min_plays


async def test_game_finished_without_collection_is_ignored(svc: Any) -> None:
    assert await svc.deliver(load_example("game.GameFinished")) is Outcome.PROCESSED
    assert await svc.sql("SELECT * FROM completions") == []


# ---------------------------------------------------------------- identity
async def test_nickname_projection_with_versions(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.published(author))["collection_id"]
    await _profile(svc, author.user_id, "neo_one", 1, "UserRegistered")
    await _profile(svc, author.user_id, "neo_two", 3, "UserProfileBasicsChanged")
    await _profile(svc, author.user_id, "neo_old", 2, "UserProfileBasicsChanged")
    card = (await svc.client.get(f"/v1/collections/{cid}")).json()
    assert card["author"] == {"kind": "user", "user_id": str(author.user_id), "nickname": "neo_two"}


async def test_user_deleted_purges_drafts_and_anonymizes_published(svc: Any) -> None:
    author, fan = svc.user(), svc.user()
    other = (await svc.published(svc.user()))["collection_id"]
    published = (await svc.published(author))["collection_id"]
    draft = (await svc.create(author))["collection_id"]
    await svc.client.put(f"/v1/collections/{other}/like", headers=author.mutate())
    await svc.client.put(f"/v1/collections/{other}/favorite", headers=author.mutate())
    await svc.client.put(f"/v1/collections/{published}/like", headers=fan.mutate())
    await svc.finish_game(author, other)
    await svc.client.post(
        f"/admin/v1/authors/{author.user_id}/restriction",
        json={"reason": "спам"},
        headers=svc.admin().mutate(),
    )
    await _profile(svc, author.user_id, "gone_user", 1, "UserRegistered")

    assert await _user_deleted(svc, author.user_id) is Outcome.PROCESSED
    assert await svc.sql("SELECT id FROM collections WHERE id = :c", c=draft) == []
    card = (await svc.client.get(f"/v1/collections/{published}")).json()
    assert card["author"] == {"kind": "deleted", "user_id": None, "nickname": None}
    assert card["likes_count"] == 1
    (event,) = await svc.events("collections.CollectionUpdated", published)
    assert_event_matches_schema(event)
    assert event.payload["author_id"] is None
    assert (await svc.client.get(f"/v1/collections/{other}")).json()["likes_count"] == 0
    for table in ("likes", "favorites", "completions", "author_restrictions"):
        assert await svc.sql(f"SELECT 1 FROM {table} WHERE user_id = :u", u=author.user_id) == []  # noqa: S608 — имя таблицы из списка выше
    # Устаревший профиль после удаления ник не возвращает.
    await _profile(svc, author.user_id, "zombie", 1, "UserProfileBasicsChanged")
    rows = await svc.sql("SELECT nickname FROM users_proj WHERE user_id = :u", u=author.user_id)
    assert rows[0].nickname is None
    # Повтор — no-op.
    assert await _user_deleted(svc, author.user_id) is Outcome.PROCESSED
    assert len(await svc.events("collections.CollectionUpdated", published)) == 1


# ---------------------------------------------------------------- social
async def _moderated(svc: Any, target: UUID | str, **changes: Any) -> Outcome:
    base = load_example("social.ContentModerated")
    payload = {
        **base.payload,
        "decision": "content_removed",
        "target_kind": "collection",
        "target_id": str(target),
        **changes,
    }
    return await svc.deliver(base.model_copy(update={"payload": payload, "event_id": uuid7()}))


async def test_content_removed_hides_collection_once(svc: Any) -> None:
    author, player = svc.user(), svc.user()
    cid = (await svc.published(author))["collection_id"]
    assert await _moderated(svc, cid, target_user_id=str(author.user_id)) is Outcome.PROCESSED
    (event,) = await svc.events("collections.CollectionUpdated", cid)
    assert_event_matches_schema(event)
    assert event.payload["status"] == "hidden"
    card = await svc.client.get(f"/v1/collections/{cid}", headers=player.headers)
    assert card.status_code == 404  # скрытый не отдаётся игрокам
    (audit,) = await svc.sql(
        "SELECT action, details FROM admin_actions WHERE target = :t", t=str(cid)
    )
    assert audit.action == "collection.hide" and audit.details["source"] == "social"
    # Повтор под другим event_id и событие по неизвестному сборнику - без изменений.
    assert await _moderated(svc, cid, target_user_id=str(author.user_id)) is Outcome.PROCESSED
    assert await _moderated(svc, uuid7()) is Outcome.PROCESSED
    assert len(await svc.events("collections.CollectionUpdated", cid)) == 1


async def test_content_removed_with_foreign_target_user_is_skipped(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.published(author))["collection_id"]
    outcome = await _moderated(svc, cid, target_user_id=str(uuid7()))
    assert outcome is Outcome.PROCESSED
    assert await svc.events("collections.CollectionUpdated", cid) == []


async def test_author_cannot_undo_moderation_hide(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.published(author))["collection_id"]
    assert await _moderated(svc, cid, target_user_id=str(author.user_id)) is Outcome.PROCESSED
    draft = await svc.client.get(f"/v1/collections/{cid}/draft", headers=author.headers)
    revision = draft.json()["revision"]
    body = draft.json()["body"]
    saved = await svc.save(author, cid, body, base=revision)
    assert_problem(saved, 409, "collections.hidden")
    published = await svc.publish(author, cid, max(revision, 1))
    assert_problem(published, 409, "collections.hidden")
    card = await svc.client.get(f"/v1/collections/{cid}", headers=svc.user().headers)
    assert card.status_code == 404


@pytest.mark.parametrize(
    "changes",
    [{"decision": "dismissed"}, {"decision": "sanctioned"}, {"target_kind": "comment"}],
    ids=["dismissed", "sanctioned", "comment"],
)
async def test_other_moderation_decisions_leave_collection_alone(
    svc: Any, changes: dict[str, str]
) -> None:
    cid = (await svc.published(svc.user()))["collection_id"]
    assert await _moderated(svc, cid, **changes) is Outcome.PROCESSED
    assert await svc.events("collections.CollectionUpdated", cid) == []


# ---------------------------------------------------------------- задачи
async def test_popular_recompute_uses_window(svc: Any) -> None:
    old = (await svc.published(svc.user()))["collection_id"]
    fresh = (await svc.published(svc.user()))["collection_id"]
    for _ in range(3):
        await svc.client.put(f"/v1/collections/{old}/like", headers=svc.user().mutate())
    svc.clock.advance(timedelta(days=8))
    await svc.client.put(f"/v1/collections/{fresh}/like", headers=svc.user().mutate())
    await svc.finish_game(svc.user(), fresh)
    assert await svc.run_job("collections.popular.recompute") is JobResult.OK
    rows = await svc.sql("SELECT id, popular_score FROM collections ORDER BY popular_score DESC")
    assert [(str(r.id), r.popular_score) for r in rows] == [(fresh, 2.0), (old, 0.0)]
    page = (await svc.client.get("/v1/collections", params={"sort": "popular"})).json()
    assert [i["collection_id"] for i in page["items"]] == [fresh, old]
    assert await svc.run_job("collections.popular.recompute") is JobResult.OK


async def test_idempotency_keys_are_purged(svc: Any) -> None:
    await assert_idempotency_purge(svc.definition, svc.infra)
