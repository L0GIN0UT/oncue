"""Набор картинки ответа в состоянии раунда: сохранение и снимки старого формата (ADR 0073)."""

from __future__ import annotations

import pytest
from game_service.domain.session import Round
from game_service.infrastructure.db.repositories import _round_json, _round_of

from gv_core.ids import uuid7
from gv_game_kernel import AssetRef

pytestmark = pytest.mark.unit


def make_round(**extra: object) -> Round:
    return Round(
        index=0,
        media_item_id=uuid7(),
        asset=AssetRef(asset_set_id=uuid7(), levels_count=6),
        levels_count=6,
        max_points=100,
        **extra,  # type: ignore[arg-type]
    )


def test_artwork_asset_survives_state_round_trip() -> None:
    artwork = AssetRef(asset_set_id=uuid7(), levels_count=1)
    restored = _round_of(_round_json(make_round(artwork_asset=artwork)))
    assert restored.artwork_asset == artwork


def test_old_snapshot_without_artwork_loads_as_none() -> None:
    raw = _round_json(make_round())
    del raw["artwork_asset"]  # снимок, записанный до ADR 0073
    assert _round_of(raw).artwork_asset is None
