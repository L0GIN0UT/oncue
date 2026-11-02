"""Область ответа по персоне в состоянии раунда и плане (пункт 49): сохранение и старые снимки."""

from __future__ import annotations

import pytest
from game_service.application.commands.play import draw_of, planned_of
from game_service.domain.session import PlannedRound, Round, RoundSubject
from game_service.infrastructure.db.repositories import (
    _planned_json,
    _planned_of,
    _round_json,
    _round_of,
)

from gv_core.ids import uuid7
from gv_game_kernel import AssetRef, PersonRef, RoundDraw
from gv_testkit.game import media_item

pytestmark = pytest.mark.unit

ARTIST = RoundSubject(id=uuid7(), name="Solo Singer", role="artist")


def make_round(**extra: object) -> Round:
    return Round(
        index=0,
        media_item_id=uuid7(),
        asset=AssetRef(asset_set_id=uuid7(), levels_count=6),
        levels_count=6,
        max_points=6,
        **extra,  # type: ignore[arg-type]
    )


def test_round_subjects_survive_state_round_trip() -> None:
    restored = _round_of(_round_json(make_round(subjects=(ARTIST,))))
    assert restored.subjects == (ARTIST,)


def test_old_round_snapshot_without_subjects_loads_empty() -> None:
    raw = _round_json(make_round())
    del raw["subjects"]  # снимок, записанный до пункта 49
    assert _round_of(raw).subjects == ()


def test_planned_round_subjects_survive_and_old_plan_loads_empty() -> None:
    planned = PlannedRound(target=uuid7(), subjects=(ARTIST,))
    raw = _planned_json(planned)
    assert _planned_of(raw).subjects == (ARTIST,)
    del raw["subjects"]
    assert _planned_of(raw).subjects == ()


def test_draw_subjects_go_to_plan_and_back_to_core_draw() -> None:
    person = PersonRef(id=ARTIST.id, name=ARTIST.name, role=ARTIST.role)
    item = media_item("Song", persons=[person])
    draw = RoundDraw(target=item, subjects=(person,))
    planned = planned_of(draw)
    assert planned.subjects == (ARTIST,)
    round_ = make_round(subjects=planned.subjects)
    assert draw_of(round_, {}, "music").subjects == (person,)
