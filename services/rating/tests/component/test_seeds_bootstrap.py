"""Загрузка сидов ladder-ов из каталога (`rating.ladders.bootstrap`, только local/ci)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rating_service.infrastructure.jobs import bootstrap_ladders

from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.component

SEEDS = Path(__file__).resolve().parents[4] / "seeds" / "rating" / "ladders"


async def test_bootstrap_creates_ladders_with_events_and_is_idempotent(rating: Any) -> None:
    created = await bootstrap_ladders(rating.container.ladder_commands, SEEDS)

    assert sorted(created) == ["film", "music"]
    published = await rating.outbox("rating.LadderPublished")
    assert sorted(e.aggregate_id for e in published) == ["film", "music"]
    for envelope in published:
        assert_event_matches_schema(envelope)
    assert len(await rating.outbox("rating.SeasonStarted")) == 2

    assert await bootstrap_ladders(rating.container.ladder_commands, SEEDS) == []
    assert len(await rating.outbox("rating.LadderPublished")) == 2


async def test_bootstrap_skips_broken_file_and_continues(rating: Any, tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("key: film\n", "utf-8")
    (tmp_path / "music.yaml").write_text((SEEDS / "music.yaml").read_text("utf-8"), "utf-8")

    assert await bootstrap_ladders(rating.container.ladder_commands, tmp_path) == ["music"]


async def test_bootstrap_does_not_touch_existing_ladder(rating: Any) -> None:
    await rating.seed("film")  # админ уже владеет ladder-ом

    assert await bootstrap_ladders(rating.container.ladder_commands, SEEDS) == ["music"]
