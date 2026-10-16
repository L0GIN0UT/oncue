"""Динамические профили (ADR 0025, план этапа 3a M5–M6) на фейках портов."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.profiles import ClueAssetsRequest
from media_pipeline_service.application.dto import CatalogTexts, MediaItemSnapshot
from media_pipeline_service.domain.dynamic_profiles import DynamicProfileStatus
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.params import params_hash
from media_pipeline_service.domain.profiles import ProfileOrigin

from gv_core import uuid7

pytestmark = pytest.mark.unit

ACTOR = UUID(int=2)
NOW = datetime(2026, 10, 16, tzinfo=UTC)
YEAR_GENRES = {"levels": [{"fields": ["year"]}, {"fields": ["genres"]}]}
TEXTS = CatalogTexts(year=2014, genres=("драма",))


def request(
    params: dict[str, Any],
    *,
    clue_type: str = "description",
    domain: str = "film",
    hash_: str | None = None,
) -> ClueAssetsRequest:
    return ClueAssetsRequest(
        request_id=uuid7(),
        mode_key=f"{domain}.random.{clue_type}",
        draft_revision=1,
        domain=domain,
        clue_type=clue_type,
        params=params,
        params_hash=hash_ or params_hash(params),
        actor_id=ACTOR,
        requested_at=NOW,
    )


async def film(media: Any, *, texts: CatalogTexts = TEXTS) -> UUID:
    item = uuid7()
    await media.sync.apply_snapshot(
        MediaItemSnapshot(item, 1, "film", "film", ItemStatus.ACTIVE, (), texts)
    )
    return item


async def test_request_creates_backfilling_profile_and_dedupes(media: Any) -> None:
    profile = await media.request_assets.execute(request(YEAR_GENRES))
    assert profile is not None
    assert profile.status is DynamicProfileStatus.BACKFILLING
    assert profile.profile_key == f"dyn_{params_hash(YEAR_GENRES)[:16]}"
    again = await media.request_assets.execute(request(YEAR_GENRES))
    assert again is not None and again.id == profile.id
    assert len(media.db.profiles) == 1


async def test_params_of_static_profile_need_no_dynamic_profile(media: Any) -> None:
    static = media.profiles.get("frame_progressive", "default")
    raw = static.params_json()
    assert await media.request_assets.execute(request(raw, clue_type="frame_progressive")) is None
    assert media.db.profiles == {}


@pytest.mark.parametrize(
    ("req", "code"),
    [
        (request({"levels": [{"fields": ["title"]}]}), "profile.invalid"),
        (request({"levels": [{"seconds": 1}]}, clue_type="hologram"), "profile.unknown_clue_type"),
        (request(YEAR_GENRES, hash_="0" * 64), "profile.hash_mismatch"),
        (
            request({"levels": [{"fields": ["year"]}], "pad": "x" * 5000}),
            "profile.params_too_large",
        ),
    ],
)
async def test_bad_requests_are_rejected_with_code(
    media: Any, req: ClueAssetsRequest, code: str
) -> None:
    profile = await media.request_assets.execute(req)
    assert profile is not None
    assert (profile.status, profile.error_code) == (DynamicProfileStatus.REJECTED, code)
    item = await film(media)
    assert await media.list_sets.execute(item, req.clue_type) == []


async def test_limit_rejects_then_revives_when_freed(media_factory: Any) -> None:
    media = media_factory(max_profiles=1)
    first = await media.request_assets.execute(request(YEAR_GENRES))
    assert first is not None and first.is_usable
    second_params = {"levels": [{"fields": ["genres"]}]}
    second = await media.request_assets.execute(request(second_params))
    assert second is not None
    assert second.error_code == "profile.limit_reached"
    # лимит освободился (профиль отклонили вручную) — повторный запрос оживляет профиль
    stored = media.db.profiles[first.id]
    stored.reject("profile.invalid", request_id=uuid7(), now=NOW)
    revived = await media.request_assets.execute(request(second_params))
    assert revived is not None and revived.id == second.id
    assert (revived.status, revived.error_code) == (DynamicProfileStatus.BACKFILLING, None)


async def test_new_items_are_planned_with_dynamic_profiles(media: Any) -> None:
    await media.request_assets.execute(request(YEAR_GENRES))
    item = await film(media)
    (view,) = await media.list_sets.execute(item, "description")
    assert view.params_hash == params_hash(YEAR_GENRES)
    assert view.profile_key.startswith("dyn_")
    await media.run_tasks()
    (ready,) = await media.list_sets.execute(item, "description")
    assert (ready.status, ready.levels_count) == ("ready", 2)


async def test_backfill_plans_existing_items_in_batches_then_activates(media: Any) -> None:
    items = sorted([await film(media) for _ in range(3)])
    profile = await media.request_assets.execute(request(YEAR_GENRES))
    assert profile is not None
    assert await media.backfill.execute() == 2  # порция harness — 2 единицы
    stored = media.db.profiles[profile.id]
    assert stored.status is DynamicProfileStatus.BACKFILLING
    assert stored.backfill_cursor == items[1]
    assert await media.backfill.execute() == 1
    assert media.db.profiles[profile.id].status is DynamicProfileStatus.ACTIVE
    assert await media.backfill.execute() == 0
    planned = [s for s in media.db.asset_sets.values() if s.clue_type == "description"]
    assert sorted(s.media_item_id for s in planned) == items
    assert len(media.queue.tasks) == 3


async def test_backfill_skips_items_without_needed_fields(media: Any) -> None:
    await film(media, texts=CatalogTexts(year=2014))  # жанров нет
    await media.request_assets.execute(request(YEAR_GENRES))
    assert await media.backfill.execute() == 1
    assert [s for s in media.db.asset_sets.values() if s.clue_type == "description"] == []


async def test_list_profiles_counts_sets(media: Any) -> None:
    await media.request_assets.execute(request(YEAR_GENRES))
    await media.request_assets.execute(request(YEAR_GENRES, hash_="1" * 64))
    await film(media)
    await media.run_tasks()
    views = await media.list_profiles.execute()
    static = {(v.clue_type, v.profile_key): v for v in views if v.origin is ProfileOrigin.STATIC}
    assert set(static) == {
        ("audio_intro", "default"), ("frame_progressive", "default"), ("poster_masked", "default"),
    }  # fmt: skip
    dynamic = [v for v in views if v.origin is ProfileOrigin.DYNAMIC]
    by_status = {v.status: v for v in dynamic}
    assert by_status["backfilling"].profile_key is None
    assert (by_status["backfilling"].ready, by_status["backfilling"].pending) == (1, 0)
    assert by_status["rejected"].error_code == "profile.hash_mismatch"
    only = await media.list_profiles.execute("description", params_hash(YEAR_GENRES))
    assert [v.status for v in only] == ["backfilling"]
