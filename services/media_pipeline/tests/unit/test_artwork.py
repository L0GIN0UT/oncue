"""Картинка ответа ``artwork`` и вид исходника ``cover`` (ADR 0073)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.dto import MediaItemSnapshot
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.generators.plugins import ArtworkGenerator
from media_pipeline_service.application.queries.sources import GetSourcePreview
from media_pipeline_service.application.sources import SourceIngestor
from media_pipeline_service.domain.clues import ArtworkParams
from media_pipeline_service.domain.errors import SourceNotFoundError, SourceTooLargeError
from media_pipeline_service.domain.media_items import ItemStatus
from media_pipeline_service.domain.packages import ROLE_KINDS
from media_pipeline_service.domain.sources import (
    FAMILY_BY_KIND,
    MediaFamily,
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
)
from media_pipeline_service.infrastructure.images import CoverNormalizer
from pydantic import ValidationError

from gv_core import uuid7
from gv_core.errors import InvalidInputError

pytestmark = pytest.mark.unit

ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000100")
ACTOR = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000002")
JPEG = b"\xff\xd8\xff\xe0" + b"jpegdata" * 4
MUSIC = {
    "source_kinds": ["cover"],
    "width": 600,
    "height": 600,
    "fit": "cover",
}
FILM = {
    "source_kinds": ["poster", "frame"],
    "width": 600,
    "height": 900,
    "fit": "cover",
}


PROFILES = {
    "version": 1,
    "profiles": [
        {"clue_type": "artwork", "profile_key": "default", "domains": ["music"], "params": MUSIC},
        {"clue_type": "artwork", "profile_key": "film", "domains": ["film"], "params": FILM},
    ],
}


def snapshot(*, domain: str = "film") -> MediaItemSnapshot:
    """Единица без исходников из каталога."""
    return MediaItemSnapshot(
        media_item_id=ITEM,
        version=1,
        domain=domain,
        kind="film" if domain == "film" else "track",
        status=ItemStatus.ACTIVE,
        sources=(),
    )


def key() -> str:
    return f"key-{uuid7().hex}"


def source(kind: SourceKind, *, position: int = 1) -> SourceMedia:
    from datetime import UTC, datetime

    return SourceMedia(
        uuid7(),
        media_item_id=ITEM,
        kind=kind,
        origin=SourceOrigin.UPLOAD,
        uri=None,
        created_at=datetime(2026, 10, 29, tzinfo=UTC),
        status=SourceStatus.READY,
        position=position,
        object_key=f"s/{uuid7().hex}.png",
        sha256="0" * 64,
        size_bytes=10,
        mime="image/png",
    )


# ---------------------------------------------------------------- виды и роли
def test_cover_is_image_source_and_package_role() -> None:
    assert FAMILY_BY_KIND[SourceKind.COVER] is MediaFamily.IMAGE
    assert ROLE_KINDS["cover"] is SourceKind.COVER


# ---------------------------------------------------------------- параметры
def test_params_have_single_level_and_expose_kinds() -> None:
    params = ArtworkParams.from_mapping(FILM)
    assert params.levels_count == 1
    assert params.source_kind_names() == ("poster", "frame")


@pytest.mark.parametrize(
    "patch",
    [
        {"source_kinds": []},
        {"source_kinds": ["cover", "cover"]},
        {"source_kinds": ["audio"]},
        {"width": 10},
        {"height": 5000},
        {"fit": "stretch"},
        {"extra": 1},
    ],
)
def test_params_reject_invalid(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ArtworkParams.from_mapping({**FILM, **patch})


def test_params_reject_missing_fields() -> None:
    with pytest.raises(ValidationError):
        ArtworkParams.from_mapping({"source_kinds": ["cover"]})


# ---------------------------------------------------------------- генератор
def test_sources_follow_kind_order_from_config() -> None:
    generator = ArtworkGenerator(processor=None)  # type: ignore[arg-type]  # не используется
    params = ArtworkParams.from_mapping(FILM)
    poster, frame = source(SourceKind.POSTER), source(SourceKind.FRAME)
    assert generator.sources_for(params, [frame, poster]) == [poster]
    assert generator.sources_for(params, [frame]) == [frame]
    assert generator.sources_for(params, [source(SourceKind.AUDIO)]) is None
    assert generator.sources_for(params, []) is None


def test_music_artwork_needs_cover_only() -> None:
    generator = ArtworkGenerator(processor=None)  # type: ignore[arg-type]
    params = ArtworkParams.from_mapping(MUSIC)
    assert generator.sources_for(params, [source(SourceKind.POSTER)]) is None
    cover = source(SourceKind.COVER)
    assert generator.sources_for(params, [cover]) == [cover]


# ---------------------------------------------------------------- профили и загрузка
async def test_profiles_accept_cover_for_music_only(media_factory: Any) -> None:
    media = media_factory(profiles=PROFILES)
    async with media.uow() as uow:
        music = {p.clue_type: p for p in await media.directory.for_domain(uow, "music")}
        film = {p.clue_type: p for p in await media.directory.for_domain(uow, "film")}
    assert SourceKind.COVER in music["artwork"].accepted_kinds
    assert film["artwork"].accepted_kinds == {SourceKind.POSTER, SourceKind.FRAME}


async def test_cover_upload_plans_artwork_for_track(media_factory: Any) -> None:
    media = media_factory(profiles=PROFILES)

    await media.sync.apply_snapshot(snapshot(domain="music"))
    result = await media.upload.execute(
        media_item_id=ITEM,
        kind=SourceKind.COVER,
        data=JPEG,
        actor_id=ACTOR,
        reason="обложка",
        idempotency_key=key(),
    )
    scheduled = {media.db.asset_sets[i].clue_type for i in result.scheduled_asset_set_ids}
    assert scheduled == {"artwork"}
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM, "artwork")
    assert view.status == "ready"
    (op,) = media.images.ops
    assert (op.max_width, op.height, op.fit) == (600, 600, "cover")


async def test_cover_not_applicable_to_film(media_factory: Any) -> None:
    media = media_factory(profiles=PROFILES)

    await media.sync.apply_snapshot(snapshot())
    with pytest.raises(InvalidInputError) as exc:
        await media.upload.execute(
            media_item_id=ITEM,
            kind=SourceKind.COVER,
            data=JPEG,
            actor_id=ACTOR,
            reason="r",
            idempotency_key=key(),
        )
    assert exc.value.code == "source.kind_not_applicable"


async def test_film_frame_fills_in_for_missing_poster(media_factory: Any) -> None:
    media = media_factory(profiles=PROFILES)

    await media.sync.apply_snapshot(snapshot())
    await media.upload.execute(
        media_item_id=ITEM,
        kind=SourceKind.FRAME,
        data=JPEG,
        actor_id=ACTOR,
        reason="кадр",
        idempotency_key=key(),
    )
    await media.run_tasks()
    (view,) = await media.list_sets.execute(ITEM, "artwork")
    assert view.status == "ready"
    assert (media.images.ops[-1].max_width, media.images.ops[-1].height) == (600, 900)


# ---------------------------------------------------------------- лимиты
async def test_cover_has_own_byte_limit() -> None:
    class Passthrough:
        async def normalize(self, data: bytes) -> Any:
            raise AssertionError("до перекодирования дойти не должно")

    ingestor = SourceIngestor(
        None,  # type: ignore[arg-type]
        {SourceKind.COVER: Passthrough(), SourceKind.POSTER: Passthrough()},  # type: ignore[dict-item]
        None,  # type: ignore[arg-type]
        max_bytes=1000,
        kind_max_bytes={SourceKind.COVER: 100},
    )
    assert ingestor.max_bytes_for(SourceKind.COVER) == 100
    assert ingestor.max_bytes_for(SourceKind.POSTER) == 1000
    with pytest.raises(SourceTooLargeError) as exc:
        ingestor.check(SourceKind.COVER, JPEG + bytes(200))
    assert exc.value.details == {"max_bytes": 100}
    ingestor.check(SourceKind.POSTER, JPEG + bytes(200))  # у постера лимит общий


async def test_cover_limit_never_exceeds_global_one() -> None:
    ingestor = SourceIngestor(
        None,  # type: ignore[arg-type]
        {},
        None,  # type: ignore[arg-type]
        max_bytes=100,
        kind_max_bytes={SourceKind.COVER: 500},
    )
    assert ingestor.max_bytes_for(SourceKind.COVER) == 100


# ---------------------------------------------------------------- просмотр
async def test_preview_returns_ready_image(media_factory: Any) -> None:
    media = media_factory(profiles=PROFILES)

    await media.sync.apply_snapshot(snapshot(domain="music"))
    uploaded = await media.upload.execute(
        media_item_id=ITEM,
        kind=SourceKind.COVER,
        data=JPEG,
        actor_id=ACTOR,
        reason="обложка",
        idempotency_key=key(),
    )
    query = GetSourcePreview(media.uow, media.storage, max_bytes=10_000)
    preview = await query.execute(uploaded.source_media_id)
    assert preview.content_type == "image/png"
    assert preview.data.startswith(b"\x89PNG")
    with pytest.raises(SourceNotFoundError):
        await query.execute(uuid7())


async def test_preview_rejects_audio_source(media_factory: Any) -> None:
    media = media_factory(
        profiles={
            "version": 1,
            "profiles": [
                *PROFILES["profiles"],
                {
                    "clue_type": "audio_intro",
                    "profile_key": "default",
                    "domains": ["music"],
                    "params": {"start_s": 0, "levels": [{"seconds": 1}]},
                },
            ],
        }
    )

    await media.sync.apply_snapshot(snapshot(domain="music"))
    audio = await media.upload.execute(
        media_item_id=ITEM,
        kind=SourceKind.AUDIO,
        data=b"ID3" + bytes(40),
        actor_id=ACTOR,
        reason="звук",
        idempotency_key=key(),
    )
    query = GetSourcePreview(media.uow, media.storage, max_bytes=10_000)
    with pytest.raises(SourceNotFoundError):
        await query.execute(audio.source_media_id)


# ---------------------------------------------------------------- нормализатор обложки
async def test_cover_normalizer_rejects_unreadable_data() -> None:
    class Inner:
        async def normalize(self, data: bytes) -> Any:
            raise AssertionError("не должен вызываться")

    normalizer = CoverNormalizer(Inner(), min_side_px=200, max_side_px=4096)  # type: ignore[arg-type]
    with pytest.raises(GenerationError) as exc:
        await normalizer.normalize(b"not an image")
    assert (exc.value.code, exc.value.retryable) == ("source.undecodable", False)
