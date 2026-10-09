"""Use-cases media_pipeline на фейках портов (фикстура ``media`` из conftest)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.generation import TaskNotVisibleError
from media_pipeline_service.application.dto import (
    GENERATE_TASK,
    CatalogSource,
    MediaItemSnapshot,
)
from media_pipeline_service.application.errors import GenerationError, GenerationRetryError
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.domain.asset_set import AssetSetStatus, AssetsFailed, AssetsReady
from media_pipeline_service.domain.errors import (
    AssetBlockedError,
    AssetNotReadyError,
    AssetSetNotFoundError,
    LevelOutOfRangeError,
    MediaItemNotFoundError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin

from gv_core import uuid7
from gv_core.errors import ConflictError, InvalidInputError, RateLimitedError
from gv_messaging.envelope import EventEnvelope
from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.unit

ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000100")
POSTER_REF = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000300")
POSTER_URI = "https://image.tmdb.org/t/p/original/abc.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"jpegdata" * 4
ACTOR = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000002")


def snapshot(
    version: int = 1,
    *,
    domain: str = "film",
    status: ItemStatus = ItemStatus.ACTIVE,
    sources: tuple[CatalogSource, ...] | None = None,
) -> MediaItemSnapshot:
    return MediaItemSnapshot(
        media_item_id=ITEM,
        version=version,
        domain=domain,
        kind="film" if domain == "film" else "track",
        status=status,
        sources=sources
        if sources is not None
        else (CatalogSource(POSTER_REF, SourceKind.POSTER, SourceOrigin.TMDB, POSTER_URI),),
    )


def to_envelope(event: Any) -> EventEnvelope:
    from gv_core.clock import SystemClock

    return EventEnvelope.from_domain_event(event, producer="media_pipeline", clock=SystemClock())


async def ready_poster(media: Any) -> UUID:
    media.fetcher.responses[POSTER_URI] = JPEG
    await media.sync.apply_snapshot(snapshot())
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM, "poster_masked")
    assert view.status == "ready"
    return view.asset_set_id


# ---------------------------------------------------------------- catalog → генерация
async def test_snapshot_plans_sets_for_domain_with_available_sources(media: Any) -> None:
    assert await media.sync.apply_snapshot(snapshot())
    sets = await media.list_sets.execute(ITEM)
    # Для фильма есть два профиля, но исходник только постер.
    assert [(s.clue_type, s.status) for s in sets] == [("poster_masked", "pending")]
    (task,) = media.queue.tasks
    assert task["task_type"] == GENERATE_TASK
    assert task["task_id"] == f"{sets[0].asset_set_id}:1"


async def test_generation_publishes_assets_ready_matching_contract(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    (event,) = media.db.events
    assert isinstance(event, AssetsReady)
    assert event.asset_set_id == asset_set_id
    assert event.levels_count == 3
    assert_event_matches_schema(to_envelope(event))
    # исходник скачан один раз, сохранён нормализованным
    assert media.fetcher.calls == [POSTER_URI]
    (source,) = media.db.sources.values()
    assert source.is_stored and source.mime == "image/png"


async def test_object_keys_are_random_and_do_not_contain_item_id(media: Any) -> None:
    await ready_poster(media)
    keys = media.storage.keys(StorageArea.ASSETS)
    assert len(keys) == 3
    for key in keys:
        assert re.fullmatch(r"a/[0-9a-f]{32}\.webp", key)
        assert ITEM.hex not in key and str(ITEM) not in key


async def test_poster_levels_get_decreasing_pixelation_and_masks(media: Any) -> None:
    await ready_poster(media)
    blocks = [op.pixel_block for op in media.images.ops]
    assert blocks == [32, 8, 1]
    assert all(op.mask_regions for op in media.images.ops)
    assert {op.max_width for op in media.images.ops} == {480}


async def test_stale_catalog_event_is_ignored(media: Any) -> None:
    assert await media.sync.apply_snapshot(snapshot(version=2))
    media.queue.tasks.clear()
    assert not await media.sync.apply_snapshot(snapshot(version=2, status=ItemStatus.BLOCKED))
    assert not await media.sync.apply_status(ITEM, 1, ItemStatus.BLOCKED)
    assert media.db.items[ITEM].status is ItemStatus.ACTIVE
    assert media.queue.tasks == []


async def test_repeated_snapshot_does_not_duplicate_sets(media: Any) -> None:
    await media.sync.apply_snapshot(snapshot(version=1))
    await media.sync.apply_snapshot(snapshot(version=2))
    assert len(await media.list_sets.execute(ITEM)) == 1
    assert len(media.queue.tasks) == 1


async def test_block_disables_sets_and_deletes_objects_then_unblock_regenerates(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    assert await media.sync.apply_status(ITEM, 2, ItemStatus.BLOCKED)
    assert media.storage.keys(StorageArea.ASSETS) == []
    with pytest.raises(AssetBlockedError):
        await media.issue_link.execute(asset_set_id, 1, "session:1")

    assert await media.sync.apply_status(ITEM, 3, ItemStatus.ACTIVE)
    assert [t["task_id"] for t in media.queue.tasks] == [f"{asset_set_id}:2"]
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM)
    assert (view.status, view.generation) == ("ready", 2)


async def test_blocked_snapshot_for_unknown_item_creates_blocked_projection(media: Any) -> None:
    assert await media.sync.apply_status(ITEM, 5, ItemStatus.BLOCKED)
    assert media.db.items[ITEM].is_blocked
    assert media.db.items[ITEM].domain is None
    assert not await media.sync.apply_snapshot(snapshot(version=4))


async def test_task_for_invisible_set_is_retried(media: Any) -> None:
    with pytest.raises(TaskNotVisibleError):
        await media.generate.execute(uuid7(), 1)


async def test_stale_generation_task_is_noop(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    events_before = len(media.db.events)
    await media.generate.execute(asset_set_id, 1)
    assert len(media.db.events) == events_before
    with pytest.raises(TaskNotVisibleError):
        await media.generate.execute(asset_set_id, 7)


async def test_retryable_failure_retries_then_publishes_failed(media_factory: Any) -> None:
    media = media_factory(max_attempts=2)
    media.fetcher.responses[POSTER_URI] = GenerationError("source.unavailable", retryable=True)
    await media.sync.apply_snapshot(snapshot())
    with pytest.raises(GenerationRetryError):
        await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM)
    assert (view.status, view.error_code) == ("pending", "source.unavailable")
    # очередь повторяет задачу
    await media.generate.execute(view.asset_set_id, 1)
    (event,) = media.db.events
    assert isinstance(event, AssetsFailed)
    assert (event.attempts, event.retryable) == (2, True)
    assert_event_matches_schema(to_envelope(event))


async def test_unsupported_source_fails_permanently(media: Any) -> None:
    media.fetcher.responses[POSTER_URI] = b"<svg onload=alert(1)>"
    await media.sync.apply_snapshot(snapshot())
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM)
    assert (view.status, view.error_code) == ("failed", "source.unsupported_type")
    (event,) = media.db.events
    assert isinstance(event, AssetsFailed) and not event.retryable


async def test_storage_crash_is_retryable_and_cleans_nothing_up_in_db(media: Any) -> None:
    media.fetcher.responses[POSTER_URI] = JPEG
    await media.sync.apply_snapshot(snapshot())
    media.storage.fail_put = True
    with pytest.raises(GenerationRetryError):
        await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM)
    assert (view.status, view.error_code) == ("pending", "internal.error")


async def test_audio_levels_cut_from_start_with_durations(media: Any) -> None:
    track = CatalogSource(uuid7(), SourceKind.AUDIO, SourceOrigin.TMDB, "https://image.tmdb.org/a")
    media.fetcher.responses["https://image.tmdb.org/a"] = b"ID3" + bytes(997)  # 10 с
    await media.sync.apply_snapshot(snapshot(domain="music", sources=(track,)))
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM)
    assert view.status == "ready"
    assert media.transcoder.cuts == [(0, 0.5), (0, 1), (0, 2)]
    aggregate = media.db.asset_sets[view.asset_set_id]
    assert [lvl.duration_ms for lvl in aggregate.levels] == [500, 1000, 2000]
    assert [lvl.size_bytes for lvl in aggregate.levels] == [50, 100, 200]
    assert all(lvl.mime == "audio/mp4" for lvl in aggregate.levels)


async def test_audio_source_too_short_fails_permanently(media: Any) -> None:
    track = CatalogSource(uuid7(), SourceKind.AUDIO, SourceOrigin.TMDB, "https://image.tmdb.org/a")
    media.fetcher.responses["https://image.tmdb.org/a"] = b"ID3" + bytes(97)  # 1 с
    await media.sync.apply_snapshot(snapshot(domain="music", sources=(track,)))
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM)
    assert (view.status, view.error_code) == ("failed", "source.too_short")


# ---------------------------------------------------------------- ссылки
async def test_issue_link_happy_path_records_journal(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    link = await media.issue_link.execute(asset_set_id, 2, "session:1")
    assert link.level == 2
    assert link.mime == "image/webp"
    assert link.expires_at == media.clock.now() + timedelta(seconds=60)
    key = media.db.asset_sets[asset_set_id].levels[1].object_key
    assert key in link.url
    (row,) = media.db.links
    assert (row["level"], row["subject"]) == (2, "session:1")


async def test_issue_link_error_contract(media: Any) -> None:
    with pytest.raises(AssetSetNotFoundError):
        await media.issue_link.execute(uuid7(), 1, "s")
    await media.sync.apply_snapshot(snapshot())
    (view,) = await media.list_sets.execute(ITEM)
    with pytest.raises(AssetNotReadyError):
        await media.issue_link.execute(view.asset_set_id, 1, "s")
    media.fetcher.responses[POSTER_URI] = JPEG
    await media.run_tasks()
    with pytest.raises(LevelOutOfRangeError):
        await media.issue_link.execute(view.asset_set_id, 4, "s")
    for _ in range(3):
        await media.issue_link.execute(view.asset_set_id, 1, "s")
    with pytest.raises(RateLimitedError) as exc:
        await media.issue_link.execute(view.asset_set_id, 1, "s")
    assert exc.value.code == "asset.rate_limited"
    # лимит — на субъект и уровень
    await media.issue_link.execute(view.asset_set_id, 2, "s")
    await media.issue_link.execute(view.asset_set_id, 1, "other")


async def test_link_journal_is_purged_after_retention(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    await media.issue_link.execute(asset_set_id, 1, "a")
    media.clock.advance(days=8)
    await media.issue_link.execute(asset_set_id, 1, "b")
    assert [r["subject"] for r in media.db.links] == ["b"]


# ---------------------------------------------------------------- admin
async def test_regenerate_starts_new_generation_and_audits(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    old_keys = set(media.storage.keys(StorageArea.ASSETS))
    view = await media.regenerate.execute(asset_set_id, actor_id=ACTOR, reason="плохой кадр")
    assert (view.status, view.generation) == ("pending", 2)
    assert media.storage.keys(StorageArea.ASSETS) == []
    (audit,) = media.db.audit
    assert (audit.action, audit.reason, audit.actor_id) == (
        "asset_set.regenerate",
        "плохой кадр",
        ACTOR,
    )
    await media.run_tasks()
    assert set(media.storage.keys(StorageArea.ASSETS)).isdisjoint(old_keys)
    with pytest.raises(AssetSetNotFoundError):
        await media.regenerate.execute(uuid7(), actor_id=ACTOR, reason="x")


async def test_regenerate_blocked_is_rejected(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    await media.sync.apply_status(ITEM, 2, ItemStatus.BLOCKED)
    with pytest.raises(AssetBlockedError):
        await media.regenerate.execute(asset_set_id, actor_id=ACTOR, reason="x")


async def test_upload_replaces_catalog_source_and_regenerates(media: Any) -> None:
    asset_set_id = await ready_poster(media)
    result = await media.upload.execute(
        media_item_id=ITEM, kind=SourceKind.POSTER, data=JPEG, actor_id=ACTOR, reason="без надписей"
    )
    assert result.scheduled_asset_set_ids == (asset_set_id,)
    assert result.mime == "image/png"
    assert media.db.asset_sets[asset_set_id].source_media_id == result.source_media_id
    assert media.db.audit[-1].action == "source.upload"
    await media.run_tasks()
    assert media.db.asset_sets[asset_set_id].status is AssetSetStatus.READY


async def test_upload_frame_creates_new_set(media: Any) -> None:
    await media.sync.apply_snapshot(snapshot(sources=()))
    result = await media.upload.execute(
        media_item_id=ITEM, kind=SourceKind.FRAME, data=JPEG, actor_id=ACTOR, reason="кадр"
    )
    (scheduled,) = result.scheduled_asset_set_ids
    await media.run_tasks()
    assert media.db.asset_sets[scheduled].clue_type == "frame_progressive"
    blurs = [op.blur_px for op in media.images.ops]
    assert blurs == sorted(blurs, reverse=True)


@pytest.mark.parametrize(
    ("kind", "data", "error"),
    [
        (SourceKind.POSTER, b"GIF89a....", UnsupportedSourceError),
        (SourceKind.POSTER, b"ID3" + bytes(10), UnsupportedSourceError),
        (SourceKind.POSTER, b"\xff\xd8\xff" + bytes(20_000), SourceTooLargeError),
        (SourceKind.AUDIO, b"ID3" + bytes(10), InvalidInputError),  # аудио не нужно фильму
    ],
)
async def test_upload_validation(
    media: Any, kind: SourceKind, data: bytes, error: type[Exception]
) -> None:
    await media.sync.apply_snapshot(snapshot(sources=()))
    with pytest.raises(error):
        await media.upload.execute(
            media_item_id=ITEM, kind=kind, data=data, actor_id=ACTOR, reason="r"
        )
    assert media.storage.keys(StorageArea.SOURCES) == []


async def test_upload_undecodable_file_is_invalid_input(media: Any) -> None:
    await media.sync.apply_snapshot(snapshot(sources=()))
    with pytest.raises(InvalidInputError) as exc:
        await media.upload.execute(
            media_item_id=ITEM,
            kind=SourceKind.POSTER,
            data=b"\xff\xd8\xffbroken",
            actor_id=ACTOR,
            reason="r",
        )
    assert exc.value.code == "source.undecodable"


async def test_upload_for_unknown_or_blocked_item(media: Any) -> None:
    with pytest.raises(MediaItemNotFoundError):
        await media.upload.execute(
            media_item_id=ITEM, kind=SourceKind.POSTER, data=JPEG, actor_id=ACTOR, reason="r"
        )
    await media.sync.apply_snapshot(snapshot(status=ItemStatus.BLOCKED))
    with pytest.raises(ConflictError) as exc:
        await media.upload.execute(
            media_item_id=ITEM, kind=SourceKind.POSTER, data=JPEG, actor_id=ACTOR, reason="r"
        )
    assert exc.value.code == "media_item.blocked"


# ---------------------------------------------------------------- идемпотентность
async def test_idempotent_executor_replays_and_detects_reuse(media: Any) -> None:
    calls = 0

    async def action() -> tuple[int, dict[str, Any]]:
        nonlocal calls
        calls += 1
        return 202, {"n": calls}

    first = await media.idempotent.run(scope="a", key="key-12345", request_hash="h1", action=action)
    second = await media.idempotent.run(
        scope="a", key="key-12345", request_hash="h1", action=action
    )
    assert first == second == (202, {"n": 1})
    with pytest.raises(ConflictError) as exc:
        await media.idempotent.run(scope="a", key="key-12345", request_hash="h2", action=action)
    assert exc.value.code == "idempotency.key_reused"


async def test_idempotent_executor_releases_key_on_error(media: Any) -> None:
    async def failing() -> tuple[int, dict[str, Any]]:
        raise AssetSetNotFoundError("нет")

    async def ok() -> tuple[int, dict[str, Any]]:
        return 200, {}

    with pytest.raises(AssetSetNotFoundError):
        await media.idempotent.run(scope="a", key="key-12345", request_hash="h", action=failing)
    assert await media.idempotent.run(scope="a", key="key-12345", request_hash="h", action=ok) == (
        200,
        {},
    )


async def test_idempotent_in_progress_conflict(media: Any) -> None:
    from media_pipeline_service.application.ports import IdempotencyRecord

    media.db.idempotency[("a", "key-12345")] = IdempotencyRecord("h", None, None)

    async def ok() -> tuple[int, dict[str, Any]]:
        return 200, {}

    with pytest.raises(ConflictError) as exc:
        await media.idempotent.run(scope="a", key="key-12345", request_hash="h", action=ok)
    assert exc.value.code == "idempotency.in_progress"


@pytest.mark.parametrize("key", [None, "", "short", "with space!!", "x" * 200])
def test_idempotency_key_format(key: str | None) -> None:
    from media_pipeline_service.application.idempotency import validate_key

    with pytest.raises(InvalidInputError):
        validate_key(key)
