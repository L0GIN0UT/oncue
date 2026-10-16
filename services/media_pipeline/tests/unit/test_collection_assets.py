"""Свои загадки сборников (план этапа 3b, M2; ADR 0029) на фейках портов."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.collections import (
    CollectionItemCustom,
    CollectionSnapshot,
)
from media_pipeline_service.application.commands.profiles import ClueAssetsRequest
from media_pipeline_service.application.dto import CatalogTexts, MediaItemSnapshot
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.domain.asset_set import AssetSetStatus, AssetsReady
from media_pipeline_service.domain.collections import CollectionStatus
from media_pipeline_service.domain.errors import CollectionContentInvalidError
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.params import params_hash
from media_pipeline_service.domain.sources import AssetScope, SourceKind, SourceOrigin
from media_pipeline_service.domain.texts import text_content, text_source_identity

from gv_core import uuid7

pytestmark = pytest.mark.unit

COLLECTION = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000c01")
SCOPE = AssetScope.collection(COLLECTION)
EMOJI_PROFILE = {
    "clue_type": "emoji",
    "profile_key": "default",
    "domains": ["music", "film"],
    "params": {"levels": [{"count": 1}, {"count": 2}, {"count": None}]},
}
PROFILES: dict[str, Any] = {"version": 1, "profiles": [EMOJI_PROFILE]}
CATALOG_EMOJI = "🚀🌌⏳"


@pytest.fixture
def media(media_factory: Any) -> Any:
    return media_factory(profiles=PROFILES)


async def film(media: Any, *, emoji: str | None = CATALOG_EMOJI) -> UUID:
    item = uuid7()
    texts = CatalogTexts(clue_texts={"emoji": emoji} if emoji else {})
    await media.sync.apply_snapshot(
        MediaItemSnapshot(item, 1, "film", "film", ItemStatus.ACTIVE, (), texts)
    )
    return item


def published(
    version: int,
    items: dict[UUID, str | None],
    *,
    status: CollectionStatus = CollectionStatus.PUBLISHED,
    domain: str = "film",
) -> CollectionSnapshot:
    return CollectionSnapshot(
        collection_id=COLLECTION,
        version=version,
        domain=domain,
        status=status,
        items=tuple(CollectionItemCustom(i, e) for i, e in items.items()),
    )


def scoped_sets(media: Any) -> list[Any]:
    return [s for s in media.db.asset_sets.values() if s.scope == SCOPE]


def scoped_sources(media: Any) -> list[Any]:
    return [s for s in media.db.sources.values() if s.scope == SCOPE]


async def test_custom_emoji_becomes_scoped_text_source_and_emoji_set(media: Any) -> None:
    item, plain = await film(media), await film(media)
    await media.run_tasks()  # каталожные наборы emoji
    catalog_sets = {s.id for s in media.db.asset_sets.values()}
    media.db.events.clear()

    result = await media.sync_collection.apply_snapshot(published(3, {item: "🚢🧊💔", plain: None}))
    assert result.applied and len(result.scheduled) == 1

    (source,) = scoped_sources(media)
    assert (source.media_item_id, source.kind, source.origin) == (
        item,
        SourceKind.TEXT,
        SourceOrigin.COLLECTION,
    )
    content = text_content(
        clue_texts={"emoji": "🚢🧊💔"}, year=None, genres=(), countries=(), directors=(), cast=()
    )
    assert source.text_content == content == {"version": 1, "emoji": "🚢🧊💔"}
    assert source.id == text_source_identity(item, content, scope=SCOPE)[0]
    # тот же текст в каталоге — другой исходник: scope входит в id
    assert source.id != text_source_identity(item, content)[0]

    (aggregate,) = scoped_sets(media)
    assert (aggregate.clue_type, aggregate.media_item_id, aggregate.source_media_ids) == (
        "emoji",
        item,
        (source.id,),
    )
    # каталожные наборы и исходники не тронуты, scoped-исходник каталог не видит
    assert catalog_sets <= set(media.db.asset_sets)
    assert all(s.scope is None for s in media.db.asset_sets.values() if s.id in catalog_sets)

    assert await media.run_tasks() == 1
    (ready,) = [e for e in media.db.events if isinstance(e, AssetsReady)]
    assert ready.scope == SCOPE and ready.media_item_id == item
    assert ready.payload()["scope"] == {"kind": "collection", "id": str(COLLECTION)}
    levels = [
        json.loads(media.storage.objects[(StorageArea.ASSETS, lvl.object_key)][0])["blocks"]
        for lvl in media.db.asset_sets[aggregate.id].levels
    ]
    assert levels == [
        [{"kind": "emoji", "text": "🚢"}],
        [{"kind": "emoji", "text": "🚢🧊"}],
        [{"kind": "emoji", "text": "🚢🧊💔"}],
    ]


async def test_catalog_events_do_not_touch_scoped_sets(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    await media.run_tasks()
    (scoped,) = scoped_sets(media)
    # правка каталожного текста перепланирует только каталожный набор
    await media.sync.apply_snapshot(
        MediaItemSnapshot(
            item,
            2,
            "film",
            "film",
            ItemStatus.ACTIVE,
            (),
            CatalogTexts(clue_texts={"emoji": "🎬🍿🎥"}),
        )
    )
    assert media.db.asset_sets[scoped.id].generation == 1
    assert media.db.asset_sets[scoped.id].status is AssetSetStatus.READY


async def test_repeat_and_stale_events_are_no_op(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(3, {item: "🚢🧊💔"}))
    await media.run_tasks()
    before = {s.id: (s.generation, s.version) for s in scoped_sets(media)}

    again = await media.sync_collection.apply_snapshot(published(4, {item: "🚢🧊💔"}))
    assert again.applied and again.scheduled == ()
    stale = await media.sync_collection.apply_snapshot(published(2, {item: "🎬🍿🎥"}))
    assert not stale.applied
    assert {s.id: (s.generation, s.version) for s in scoped_sets(media)} == before
    assert media.queue.tasks == []


async def test_edit_regenerates_and_removal_deletes_set_and_objects(media: Any) -> None:
    item, other = await film(media), await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔", other: "🧙‍♂️💍🌋"}))
    await media.run_tasks()
    by_item = {s.media_item_id: s for s in scoped_sets(media)}

    # правка эмодзи — новый исходник, тот же набор, новое поколение
    await media.sync_collection.apply_snapshot(published(2, {item: "🚢🧊🎻", other: "🧙‍♂️💍🌋"}))
    edited = media.db.asset_sets[by_item[item].id]
    assert (edited.generation, edited.status) == (2, AssetSetStatus.PENDING)
    assert {s.media_item_id for s in scoped_sources(media)} == {item, other}
    old_keys = [lvl.object_key for lvl in by_item[item].levels]
    assert all((StorageArea.ASSETS, k) not in media.storage.objects for k in old_keys)
    await media.run_tasks()

    # эмодзи убрали у одной единицы — её набор и исходник удалены вместе с объектами
    removed_keys = [lvl.object_key for lvl in media.db.asset_sets[by_item[other].id].levels]
    result = await media.sync_collection.apply_snapshot(published(3, {item: "🚢🧊🎻", other: None}))
    assert result.deleted_sets == 1
    assert [s.media_item_id for s in scoped_sets(media)] == [item]
    assert [s.media_item_id for s in scoped_sources(media)] == [item]
    assert all((StorageArea.ASSETS, k) not in media.storage.objects for k in removed_keys)


async def test_hidden_and_deleted_collection_drop_scoped_sets(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    await media.run_tasks()

    hidden = await media.sync_collection.apply_snapshot(
        published(2, {item: "🚢🧊💔"}, status=CollectionStatus.HIDDEN)
    )
    assert hidden.deleted_sets == 1
    assert scoped_sets(media) == [] and scoped_sources(media) == []
    # снова опубликован — наборы строятся заново
    await media.sync_collection.apply_snapshot(published(3, {item: "🚢🧊💔"}))
    assert len(scoped_sets(media)) == 1
    await media.run_tasks()

    deleted = await media.sync_collection.apply_deleted(COLLECTION, 4)
    assert deleted.deleted_sets == 1 and scoped_sets(media) == []
    assert media.db.collections[COLLECTION].status is CollectionStatus.DELETED
    # запоздавшая версия не воскрешает удалённый сборник
    late = await media.sync_collection.apply_snapshot(published(3, {item: "🚢🧊💔"}))
    assert not late.applied and scoped_sets(media) == []
    # каталожный набор единицы на месте
    assert [s.clue_type for s in media.db.asset_sets.values()] == ["emoji"]


@pytest.mark.parametrize(
    "emoji",
    [
        "abc",  # текст
        "🚢 🧊",  # пробел
        "🇷",  # непарный региональный индикатор
        "🚢‍",  # голый ZWJ в конце
        "😀" * 13,  # больше 12 графем
        "1",  # цифра без keycap
    ],
)
async def test_emoji_outside_strict_set_is_rejected(media: Any, emoji: str) -> None:
    item = await film(media)
    with pytest.raises(CollectionContentInvalidError) as caught:
        await media.sync_collection.apply_snapshot(published(1, {item: emoji}))
    assert caught.value.details == {"position": 0, "reason": "emoji_invalid"}
    assert scoped_sources(media) == [] and COLLECTION not in media.db.collections


async def test_hiding_drops_sets_even_with_broken_content(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    hidden = await media.sync_collection.apply_snapshot(
        published(2, {item: "abc"}, status=CollectionStatus.HIDDEN)
    )
    assert hidden.applied and scoped_sets(media) == [] and scoped_sources(media) == []


async def test_duplicate_item_is_rejected(media: Any) -> None:
    item = await film(media)
    snapshot = CollectionSnapshot(
        COLLECTION,
        1,
        "film",
        CollectionStatus.PUBLISHED,
        (CollectionItemCustom(item, "🚢"), CollectionItemCustom(item, None)),
    )
    with pytest.raises(CollectionContentInvalidError):
        await media.sync_collection.apply_snapshot(snapshot)


async def test_unknown_or_foreign_domain_item_is_skipped(media: Any) -> None:
    unknown = uuid7()
    track = uuid7()
    await media.sync.apply_snapshot(
        MediaItemSnapshot(track, 1, "music", "track", ItemStatus.ACTIVE, (), CatalogTexts())
    )
    item = await film(media)
    result = await media.sync_collection.apply_snapshot(
        published(1, {unknown: "🚢🧊💔", track: "🎸🎤🥁", item: "🚢🧊💔"})
    )
    assert result.applied
    assert {s.media_item_id for s in scoped_sources(media)} == {item}


async def test_emoji_shorter_than_profile_gets_no_set_and_old_set_is_dropped(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    await media.run_tasks()
    # профилю нужно не меньше 2 эмодзи: прежний набор показывал бы старые эмодзи — удаляется
    result = await media.sync_collection.apply_snapshot(published(2, {item: "🚢"}))
    assert result.scheduled == () and result.deleted_sets == 1
    assert scoped_sets(media) == []
    (source,) = scoped_sources(media)
    assert source.text_content == {"version": 1, "emoji": "🚢"}


async def test_block_disables_scoped_sets_and_unblock_restores(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    await media.run_tasks()
    await media.sync.apply_status(item, 2, ItemStatus.BLOCKED)
    assert {s.status for s in media.db.asset_sets.values()} == {AssetSetStatus.DISABLED}
    await media.sync.apply_status(item, 3, ItemStatus.ACTIVE)
    (scoped,) = scoped_sets(media)
    assert (scoped.status, scoped.generation) == (AssetSetStatus.PENDING, 2)


async def test_backfill_of_dynamic_emoji_profile_covers_collections(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    await media.run_tasks()
    params = {"levels": [{"count": 3}]}
    dynamic = await media.request_assets.execute(
        ClueAssetsRequest(
            request_id=uuid7(),
            mode_key="film.collection.emoji",
            draft_revision=1,
            domain="film",
            clue_type="emoji",
            params=params,
            params_hash=params_hash(params),
            actor_id=UUID(int=2),
            requested_at=datetime(2026, 10, 16, tzinfo=UTC),
        )
    )
    assert dynamic is not None
    await media.backfill.execute()
    planned = {
        (s.scope, s.params_hash)
        for s in media.db.asset_sets.values()
        if s.params_hash == params_hash(params)
    }
    assert planned == {(None, params_hash(params)), (SCOPE, params_hash(params))}


async def test_admin_sources_list_hides_scoped_and_orders_by_kind_position(media: Any) -> None:
    item = await film(media)
    await media.sync_collection.apply_snapshot(published(1, {item: "🚢🧊💔"}))
    (catalog_text,) = [s for s in media.db.sources.values() if s.scope is None]
    views = await media.list_sources.execute(item)
    assert [v.source_media_id for v in views] == [catalog_text.id]
    assert (views[0].kind, views[0].origin) == (SourceKind.TEXT, SourceOrigin.CATALOG)
    assert await media.list_sources.execute(uuid7()) == []
