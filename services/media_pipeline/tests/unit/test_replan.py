"""Пересоздание наборов статического профиля после правки ``asset_profiles.yaml``."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.catalog import PlanAssetSets
from media_pipeline_service.application.commands.profiles import ReplanStaticProfiles
from media_pipeline_service.application.dto import CatalogSource, MediaItemSnapshot
from media_pipeline_service.application.profiles import ProfileDirectory
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin

from gv_core import uuid7
from gv_core.errors import ConflictError, InvalidInputError

pytestmark = pytest.mark.unit

ACTOR = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000002")
JPEG = b"\xff\xd8\xff\xe0" + b"jpegdata" * 4
ITEMS = [UUID(f"0192a3b4-c5d6-7e8f-9a0b-0000000004{n:02d}") for n in range(3)]
OLD_LEVELS = [
    {"blur_px": 48, "crop": 0.35, "max_width": 640},
    {"blur_px": 0, "crop": 1, "max_width": 640},
]
NEW_LEVELS = [
    {"blur_px": 25, "crop": 0.35, "max_width": 640},
    {"blur_px": 10, "crop": 1, "max_width": 640},
    {"blur_px": 0, "crop": 1, "max_width": 640},
]


def profiles_doc(levels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "profiles": [
            {
                "clue_type": "frame_progressive",
                "profile_key": "default",
                "domains": ["film"],
                "params": {"levels": deepcopy(levels)},
            }
        ],
    }


def key() -> str:
    return f"key-{uuid7().hex}"


async def _load_items(media: Any) -> None:
    for n, item in enumerate(ITEMS):
        uri = f"https://image.tmdb.org/t/p/original/f{n}.jpg"
        media.fetcher.responses[uri] = JPEG
        ref = UUID(f"0192a3b4-c5d6-7e8f-9a0b-0000000005{n:02d}")
        await media.sync.apply_snapshot(
            MediaItemSnapshot(
                media_item_id=item,
                version=1,
                domain="film",
                kind="film",
                status=ItemStatus.ACTIVE,
                sources=(CatalogSource(ref, SourceKind.FRAME, SourceOrigin.TMDB, uri),),
            )
        )
    await media.run_tasks()


def deployed(media: Any, levels: list[dict[str, Any]], *, max_limit: int = 10) -> Any:
    """Сервис после выкладки нового ``asset_profiles.yaml``: тот же БД и S3, другой каталог."""
    directory = ProfileDirectory(media.registry.build_catalog(profiles_doc(levels)), media.registry)
    plan = PlanAssetSets(
        media.uow,
        media.clock,
        directory,
        media.queue,
        media.storage,
        registry=media.registry,
    )
    return ReplanStaticProfiles(
        media.uow, media.clock, directory, plan, media.storage, max_limit=max_limit
    )


async def _sets(media: Any, item: UUID) -> list[Any]:
    return await media.list_sets.execute(item, "frame_progressive")


async def test_replan_creates_sets_for_new_hash_and_keeps_old_ones(media_factory: Any) -> None:
    """Новые параметры → новый params_hash → новые наборы; старые остаются готовыми."""
    media = media_factory(profiles=profiles_doc(OLD_LEVELS))
    await _load_items(media)
    old = {i: await _sets(media, i) for i in ITEMS}
    assert all([s.status for s in sets] == ["ready"] for sets in old.values())

    replan = deployed(media, NEW_LEVELS)
    batch = await replan.execute(
        clue_type="frame_progressive",
        domain="film",
        after=None,
        limit=10,
        actor_id=ACTOR,
        reason="блюр 25..0",
        idempotency_key=key(),
    )
    assert (batch.processed, batch.scheduled, batch.next_after) == (3, 3, None)
    assert len(media.queue.tasks) == 3
    await media.run_tasks()
    for item in ITEMS:
        sets = await _sets(media, item)
        assert len(sets) == 2
        assert {s.status for s in sets} == {"ready"}
        assert {s.levels_count for s in sets} == {2, 3}
        assert {s.asset_set_id for s in old[item]} <= {s.asset_set_id for s in sets}
    (audit, *_) = media.db.audit
    assert (audit.action, audit.reason, audit.actor_id) == (
        "asset_sets.replan",
        "блюр 25..0",
        ACTOR,
    )


async def test_replan_is_idempotent_and_does_not_duplicate(media_factory: Any) -> None:
    media = media_factory(profiles=profiles_doc(OLD_LEVELS))
    await _load_items(media)
    replan = deployed(media, NEW_LEVELS)
    args: dict[str, Any] = {
        "clue_type": "frame_progressive",
        "domain": "film",
        "after": None,
        "limit": 10,
        "actor_id": ACTOR,
        "reason": "блюр",
    }
    same_key = key()
    first = await replan.execute(**args, idempotency_key=same_key)
    await media.run_tasks()
    # Повтор того же запроса отдаёт сохранённый ответ и ничего не ставит.
    assert await replan.execute(**args, idempotency_key=same_key) == first
    assert media.queue.tasks == []
    # Новый запрос с другим ключом: наборы уже есть — ничего не планируется.
    again = await replan.execute(**args, idempotency_key=key())
    assert (again.processed, again.scheduled) == (3, 0)
    # Тот же ключ с другими параметрами — конфликт.
    with pytest.raises(ConflictError):
        await replan.execute(**{**args, "limit": 5}, idempotency_key=same_key)


async def test_replan_walks_in_batches_by_cursor(media_factory: Any) -> None:
    media = media_factory(profiles=profiles_doc(OLD_LEVELS))
    await _load_items(media)
    replan = deployed(media, NEW_LEVELS)
    after: UUID | None = None
    seen: list[int] = []
    for _ in range(5):
        batch = await replan.execute(
            clue_type="frame_progressive",
            domain="film",
            after=after,
            limit=2,
            actor_id=ACTOR,
            reason="порции",
            idempotency_key=key(),
        )
        seen.append(batch.processed)
        after = batch.next_after
        if after is None:
            break
    assert seen == [2, 1]
    assert after is None


async def test_replan_rejects_bad_input(media_factory: Any) -> None:
    media = media_factory(profiles=profiles_doc(OLD_LEVELS))
    replan = deployed(media, NEW_LEVELS, max_limit=5)
    base: dict[str, Any] = {
        "clue_type": "frame_progressive",
        "domain": "film",
        "after": None,
        "limit": 5,
        "actor_id": ACTOR,
        "reason": "x",
    }
    for bad in ({"limit": 0}, {"limit": 6}, {"clue_type": "nope"}, {"domain": "music"}):
        with pytest.raises(InvalidInputError):
            await replan.execute(**{**base, **bad}, idempotency_key=key())
