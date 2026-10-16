"""Жизненный цикл AssetSet, проекция единицы, исходники, ключи и политика ссылок."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from media_pipeline_service.domain.asset_set import (
    AssetLevel,
    AssetSet,
    AssetSetStatus,
    AssetsFailed,
    AssetsReady,
)
from media_pipeline_service.domain.clues import PosterMaskedParams
from media_pipeline_service.domain.errors import (
    AssetBlockedError,
    AssetNotReadyError,
    InvalidProfileError,
    LevelOutOfRangeError,
)
from media_pipeline_service.domain.links import LinkPolicy, asset_object_key, source_object_key
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.profiles import AssetProfile, AssetProfileCatalog
from media_pipeline_service.domain.sources import (
    MediaFamily,
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
    best_source,
    detect_type,
)

from gv_core import uuid7
from gv_core.errors import DomainError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000100")


def _profile(levels: int = 3) -> AssetProfile:
    params = PosterMaskedParams.from_mapping(
        {"levels": [{"pixel_block": 2 ** (levels - i)} for i in range(levels)]}
    )
    return AssetProfile(
        clue_type="poster_masked",
        profile_key="default",
        domains=frozenset({"film"}),
        source_kind=SourceKind.POSTER,
        params=params,
    )


def _levels(n: int) -> list[AssetLevel]:
    return [
        AssetLevel(
            level=i,
            object_key=asset_object_key(f"{i:032x}", "webp"),
            mime="image/webp",
            size_bytes=10,
        )
        for i in range(1, n + 1)
    ]


def _planned(levels: int = 3) -> AssetSet:
    return AssetSet.plan(
        uuid7(), media_item_id=ITEM, profile=_profile(levels), source_media_ids=(uuid7(),), now=NOW
    )


def test_plan_starts_pending_and_new() -> None:
    aggregate = _planned()
    assert aggregate.status is AssetSetStatus.PENDING
    assert aggregate.is_new
    assert aggregate.generation == 1
    assert aggregate.levels_count == 3
    assert aggregate.params_hash == _profile().params_hash


def test_happy_path_records_assets_ready_with_item_event_version() -> None:
    aggregate = _planned()
    aggregate.start_attempt(1, NOW)
    assert aggregate.status is AssetSetStatus.PROCESSING
    aggregate.complete(1, _levels(3), NOW, event_version=7)
    (event,) = aggregate.pull_events()
    assert isinstance(event, AssetsReady)
    assert event.aggregate_id == str(ITEM)
    assert event.levels_count == 3
    # версия события — из счётчика единицы, а не версия агрегата-набора
    assert (event.version, aggregate.version) == (7, 3)
    assert aggregate.level(2).level == 2


def test_complete_rejects_wrong_level_numbers() -> None:
    aggregate = _planned()
    aggregate.start_attempt(1, NOW)
    with pytest.raises(DomainError) as exc:
        aggregate.complete(1, _levels(2), NOW, event_version=1)
    assert exc.value.code == "asset_set.levels_mismatch"


def test_retryable_failure_returns_to_pending_until_attempts_exhausted() -> None:
    aggregate = _planned()
    aggregate.start_attempt(1, NOW)
    assert (
        aggregate.fail(
            1, "source.unavailable", retryable=True, max_attempts=2, now=NOW, event_version=4
        )
        is False
    )
    assert aggregate.status is AssetSetStatus.PENDING
    assert aggregate.pending_events == ()
    aggregate.start_attempt(1, NOW)
    assert (
        aggregate.fail(
            1, "source.unavailable", retryable=True, max_attempts=2, now=NOW, event_version=4
        )
        is True
    )
    (event,) = aggregate.pull_events()
    assert isinstance(event, AssetsFailed)
    assert (event.attempts, event.retryable, event.error_code) == (2, True, "source.unavailable")
    assert event.version == 4
    assert aggregate.status is AssetSetStatus.FAILED


def test_permanent_failure_is_final_on_first_attempt() -> None:
    aggregate = _planned()
    aggregate.start_attempt(1, NOW)
    assert aggregate.fail(
        1, "source.forbidden", retryable=False, max_attempts=5, now=NOW, event_version=1
    )
    assert aggregate.status is AssetSetStatus.FAILED


def test_stale_generation_is_rejected() -> None:
    aggregate = _planned()
    aggregate.start_attempt(1, NOW)
    aggregate.complete(1, _levels(3), NOW, event_version=1)
    old = aggregate.regenerate(NOW)
    assert [lvl.level for lvl in old] == [1, 2, 3]
    assert aggregate.generation == 2
    assert not aggregate.is_current(1)
    with pytest.raises(DomainError) as exc:
        aggregate.start_attempt(1, NOW)
    assert exc.value.code == "asset_set.stale_generation"


def test_level_access_errors_follow_contract_order() -> None:
    aggregate = _planned()
    with pytest.raises(AssetNotReadyError):
        aggregate.level(1)
    aggregate.start_attempt(1, NOW)
    aggregate.complete(1, _levels(3), NOW, event_version=1)
    with pytest.raises(LevelOutOfRangeError):
        aggregate.level(4)
    with pytest.raises(LevelOutOfRangeError):
        aggregate.level(0)
    removed = aggregate.disable(NOW)
    assert len(removed) == 3
    assert aggregate.levels == ()
    with pytest.raises(AssetBlockedError):
        aggregate.level(1)
    with pytest.raises(AssetBlockedError):
        aggregate.regenerate(NOW)
    assert aggregate.disable(NOW) == []


def test_restore_starts_new_generation() -> None:
    aggregate = _planned()
    aggregate.disable(NOW)
    aggregate.restore(NOW)
    assert aggregate.status is AssetSetStatus.PENDING
    assert aggregate.generation == 2
    aggregate.restore(NOW)  # повтор ничего не меняет
    assert aggregate.generation == 2


def test_regenerate_switches_source() -> None:
    aggregate = _planned()
    new_source = uuid7()
    aggregate.regenerate(NOW, source_media_ids=(new_source,))
    assert aggregate.source_media_id == new_source


# ---------------------------------------------------------------- профили
def test_profile_catalog_rejects_duplicates_and_filters_by_domain() -> None:
    profile = _profile()
    with pytest.raises(InvalidProfileError):
        AssetProfileCatalog([profile, profile])
    catalog = AssetProfileCatalog([profile])
    assert catalog.for_domain("film") == [profile]
    assert catalog.for_domain("music") == []
    assert catalog.get("poster_masked", "default") is profile
    assert len(catalog) == 1


def test_profile_rejects_bad_keys() -> None:
    with pytest.raises(InvalidProfileError):
        AssetProfile(
            clue_type="Poster",
            profile_key="default",
            domains=frozenset({"film"}),
            source_kind=SourceKind.POSTER,
            params=_profile().params,
        )


# ---------------------------------------------------------------- проекция единицы
def test_item_state_drops_stale_versions() -> None:
    state = MediaItemState(
        ITEM, domain="film", kind="film", status=ItemStatus.ACTIVE, version=2, updated_at=NOW
    )
    assert not state.apply_status(version=2, status=ItemStatus.BLOCKED, now=NOW)
    assert not state.apply_snapshot(
        version=1, domain="film", kind="film", status=ItemStatus.BLOCKED, now=NOW
    )
    assert not state.is_blocked
    assert state.apply_status(version=3, status=ItemStatus.BLOCKED, now=NOW)
    assert state.is_blocked
    assert state.version == 3


# ---------------------------------------------------------------- исходники
def test_best_source_prefers_upload_then_newest() -> None:
    def source(
        origin: SourceOrigin, minutes: int, kind: SourceKind = SourceKind.POSTER
    ) -> SourceMedia:
        return SourceMedia(
            uuid7(),
            media_item_id=ITEM,
            kind=kind,
            origin=origin,
            uri=None,
            created_at=NOW + timedelta(minutes=minutes),
        )

    old_tmdb, new_tmdb = source(SourceOrigin.TMDB, 0), source(SourceOrigin.TMDB, 5)
    upload = source(SourceOrigin.UPLOAD, 1)
    assert best_source([old_tmdb, new_tmdb], SourceKind.POSTER) is new_tmdb
    assert best_source([old_tmdb, upload, new_tmdb], SourceKind.POSTER) is upload
    assert best_source([upload], SourceKind.AUDIO) is None
    upload.status = SourceStatus.FAILED
    assert best_source([upload], SourceKind.POSTER) is None


def test_source_mark_stored() -> None:
    src = SourceMedia(
        uuid7(),
        media_item_id=ITEM,
        kind=SourceKind.AUDIO,
        origin=SourceOrigin.UPLOAD,
        uri=None,
        created_at=NOW,
    )
    assert not src.is_stored
    with pytest.raises(DomainError):
        src.mark_stored(object_key="s/x", sha256="0" * 64, size_bytes=0, mime="audio/flac")
    src.mark_stored(object_key="s/x", sha256="0" * 64, size_bytes=5, mime="audio/flac")
    assert src.is_stored


@pytest.mark.parametrize(
    ("head", "name", "family"),
    [
        (b"\xff\xd8\xff\xe0" + b"\0" * 8, "jpeg", MediaFamily.IMAGE),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 4, "png", MediaFamily.IMAGE),
        (b"RIFF\0\0\0\0WEBPVP8 ", "webp", MediaFamily.IMAGE),
        (b"RIFF\0\0\0\0WAVEfmt ", "wav", MediaFamily.AUDIO),
        (b"fLaC\0\0\0\0", "flac", MediaFamily.AUDIO),
        (b"OggS\0\0\0\0", "ogg", MediaFamily.AUDIO),
        (b"ID3\x04\0\0\0\0", "mp3", MediaFamily.AUDIO),
        (b"\xff\xfb\x90\x00", "mp3", MediaFamily.AUDIO),
        (b"\0\0\0\x20ftypM4A ", "mp4", MediaFamily.AUDIO),
    ],
)
def test_detect_type_by_signature(head: bytes, name: str, family: MediaFamily) -> None:
    detected = detect_type(head)
    assert detected is not None
    assert (detected.name, detected.family) == (name, family)


@pytest.mark.parametrize("head", [b"", b"<svg xmlns=", b"GIF89a", b"%PDF-1.7", b"MZ\x90\x00"])
def test_detect_type_rejects_unknown(head: bytes) -> None:
    assert detect_type(head) is None


# ---------------------------------------------------------------- ключи и политика ссылок
@given(st.binary(min_size=16, max_size=16), st.sampled_from(["webp", "m4a", "png", "flac"]))
def test_object_keys_are_random_tokens_only(token: bytes, ext: str) -> None:
    key = asset_object_key(token.hex(), ext)
    assert key == f"a/{token.hex()}.{ext}"
    assert source_object_key(token.hex(), ext).startswith("s/")


@pytest.mark.parametrize("token", ["", "abc", "0" * 31, "G" * 32, str(ITEM)])
def test_object_key_rejects_non_random_token(token: str) -> None:
    with pytest.raises(ValueError, match="32 hex"):
        asset_object_key(token, "webp")


def test_object_key_rejects_bad_extension() -> None:
    with pytest.raises(ValueError, match="расширение"):
        asset_object_key("0" * 32, "../x")


def test_link_policy_expiry_and_rate_key() -> None:
    policy = LinkPolicy(ttl_s=60, max_per_window=3, window_s=60)
    assert policy.expires_at(NOW.replace(microsecond=999)) == NOW + timedelta(seconds=60)
    key = LinkPolicy.rate_key("user:42", ITEM, 2)
    assert "user:42" not in key
    assert key.endswith(f"{ITEM.hex}:2")
    with pytest.raises(ValueError, match="ttl_s"):
        LinkPolicy(ttl_s=0, max_per_window=1, window_s=1)
    with pytest.raises(ValueError, match="положительными"):
        LinkPolicy(ttl_s=60, max_per_window=0, window_s=1)
