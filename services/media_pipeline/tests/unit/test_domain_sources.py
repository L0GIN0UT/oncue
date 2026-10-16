"""Исходники: виды и семейства, позиции, выбор лучшего исходника (этап 3a, M2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from media_pipeline_service.domain.sources import (
    FAMILY_BY_KIND,
    AssetScope,
    MediaFamily,
    ScopeKind,
    SourceKind,
    SourceMedia,
    SourceOrigin,
    SourceStatus,
    best_source,
    detect_type,
    sources_by_position,
)

from gv_core import uuid7
from gv_core.errors import DomainError

pytestmark = pytest.mark.unit

ITEM = UUID(int=1)
NOW = datetime(2026, 10, 16, tzinfo=UTC)


def source(
    kind: SourceKind = SourceKind.FRAME,
    *,
    origin: SourceOrigin = SourceOrigin.TMDB,
    position: int = 1,
    at: datetime = NOW,
    status: SourceStatus = SourceStatus.REGISTERED,
) -> SourceMedia:
    return SourceMedia(
        uuid7(),
        media_item_id=ITEM,
        kind=kind,
        origin=origin,
        uri="https://image.tmdb.org/x.jpg",
        created_at=at,
        status=status,
        position=position,
    )


def test_every_kind_has_family_and_text_is_never_detected() -> None:
    assert set(FAMILY_BY_KIND) == set(SourceKind)
    assert FAMILY_BY_KIND[SourceKind.SOUNDTRACK] is MediaFamily.AUDIO
    assert FAMILY_BY_KIND[SourceKind.AUDIO_CLIP] is MediaFamily.AUDIO
    for head in (b'{"a": 1}', b"plain text", b"\xff\xd8\xff", b"fLaC"):
        detected = detect_type(head)
        assert detected is None or detected.family is not MediaFamily.TEXT


@pytest.mark.parametrize("position", [0, 101])
def test_position_range(position: int) -> None:
    with pytest.raises(DomainError):
        source(position=position)
    ok = source()
    with pytest.raises(DomainError):
        ok.move_to(position)


def test_text_content_only_for_text_kind() -> None:
    with pytest.raises(DomainError):
        SourceMedia(
            uuid7(), media_item_id=ITEM, kind=SourceKind.FRAME, origin=SourceOrigin.TMDB,
            uri=None, created_at=NOW, text_content={"a": 1},
        )  # fmt: skip
    with pytest.raises(DomainError):
        SourceMedia(
            uuid7(), media_item_id=ITEM, kind=SourceKind.TEXT, origin=SourceOrigin.TMDB,
            uri=None, created_at=NOW,
        )  # fmt: skip


def test_scope_only_for_text_source_and_sets_collection_origin() -> None:
    """ADR 0029: свои загадки сборника — только текстовый исходник с ``origin=collection``."""
    scope = AssetScope.collection(uuid7())
    with pytest.raises(DomainError, match="scope"):
        SourceMedia(
            uuid7(), media_item_id=ITEM, kind=SourceKind.FRAME, origin=SourceOrigin.TMDB,
            uri=None, created_at=NOW, scope=scope,
        )  # fmt: skip
    scoped, catalog = (
        SourceMedia.text(
            uuid7(),
            media_item_id=ITEM,
            content={"emoji": "🚢"},
            sha256="0" * 64,
            size_bytes=1,
            created_at=NOW,
            scope=owner,
        )
        for owner in (scope, None)
    )
    assert (scoped.scope, scoped.origin) == (scope, SourceOrigin.COLLECTION)
    assert (catalog.scope, catalog.origin) == (None, SourceOrigin.CATALOG)
    assert scope.kind is ScopeKind.COLLECTION


def test_best_source_prefers_admin_upload_then_fresh_then_lower_position() -> None:
    tmdb_1, tmdb_2 = source(position=2), source(position=1)
    assert best_source([tmdb_1, tmdb_2], SourceKind.FRAME) is tmdb_2
    seed = source(origin=SourceOrigin.SEED, at=NOW - timedelta(days=1))
    assert best_source([tmdb_1, tmdb_2, seed], SourceKind.FRAME) is seed
    failed = source(origin=SourceOrigin.UPLOAD, status=SourceStatus.FAILED)
    assert best_source([tmdb_1, failed], SourceKind.FRAME) is tmdb_1
    assert best_source([tmdb_1], SourceKind.POSTER) is None


def test_sources_by_position_picks_best_per_position() -> None:
    a1, a2 = source(position=1), source(position=2)
    upload_2 = source(origin=SourceOrigin.UPLOAD, position=2, at=NOW - timedelta(days=1))
    poster = source(SourceKind.POSTER, position=3)
    failed = source(position=3, status=SourceStatus.FAILED)
    assert sources_by_position([a1, a2, upload_2, poster, failed], SourceKind.FRAME) == {
        1: a1,
        2: upload_2,
    }
