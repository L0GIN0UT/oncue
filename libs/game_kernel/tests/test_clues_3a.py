"""Загадки этапа 3a: домены, способ подачи, подсказки уровней (seconds / frame / fields / count)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from gv_game_kernel import LevelSpec
from gv_game_kernel.builtin import (
    AudioBlackoutClue,
    AudioReverseClue,
    DescriptionClue,
    DescriptionParams,
    EmojiClue,
    EmojiParams,
    FrameSequenceClue,
    FrameSequenceParams,
    LeveledClue,
    SoundtrackClue,
)
from gv_testkit.game import FakeAssetPort, asset_ref

pytestmark = pytest.mark.unit

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[3] / "contracts" / "clues" / "params_hash.golden.json"
    ).read_text(encoding="utf-8")
)


def _profile(name: str) -> dict[str, Any]:
    params: dict[str, Any] = next(v for v in GOLDEN["vectors"] if v["name"] == name)["params"]
    return params


def _params(clue: LeveledClue, name: str) -> BaseModel:
    return clue.params_schema.model_validate(_profile(name))


@pytest.mark.parametrize(
    ("clue", "domains", "kind"),
    [
        (AudioReverseClue(), {"music"}, "audio"),
        (SoundtrackClue(), {"film"}, "audio"),
        (AudioBlackoutClue(), {"film"}, "audio"),
        (FrameSequenceClue(), {"film"}, "image"),
        (DescriptionClue(), {"film"}, "text"),
        (EmojiClue(), {"music", "film"}, "text"),
    ],
    ids=lambda x: getattr(x, "key", None),
)
def test_domains_and_view_kind(clue: LeveledClue, domains: set[str], kind: str) -> None:
    assert clue.domains == frozenset(domains)
    assert clue.view_kind == kind
    assert clue.supported_answer_formats == frozenset({"classic", "pick"})


@pytest.mark.parametrize(
    "clue", [AudioReverseClue(), SoundtrackClue(), AudioBlackoutClue()], ids=lambda c: c.key
)
def test_audio_levels_expose_seconds(clue: LeveledClue) -> None:
    levels = clue.levels(_params(clue, f"profile:{clue.key}:default"))
    assert [lvl.hint for lvl in levels] == [{"seconds": s} for s in (0.5, 1, 2, 4, 8, 15)]


def test_frame_sequence_hint_is_frame_without_width() -> None:
    clue = FrameSequenceClue()
    levels = clue.levels(_params(clue, "profile:frame_sequence:default"))
    assert levels[0] == LevelSpec(1, {"frame": 1})
    assert [lvl.hint["frame"] for lvl in levels] == [1, 2, 3, 4, 5, 6]


def test_description_fields_accumulate() -> None:
    clue = DescriptionClue()
    hints = [
        lvl.hint["fields"] for lvl in clue.levels(_params(clue, "profile:description:default"))
    ]
    assert hints[0] == ["tagline"]
    assert hints[1] == ["tagline", "plot_masked"]
    assert hints[2] == ["tagline", "plot_masked", "year", "genres"]
    assert hints[-1] == [
        "tagline",
        "plot_masked",
        "year",
        "genres",
        "country",
        "director",
        "cast_top3",
    ]


def test_description_repeated_field_is_not_duplicated() -> None:
    params = DescriptionParams.model_validate(
        {"levels": [{"fields": ["year"]}, {"fields": ["year", "genres"]}]}
    )
    hints = [lvl.hint for lvl in DescriptionClue().levels(params)]
    assert hints == [{"fields": ["year"]}, {"fields": ["year", "genres"]}]


def test_emoji_count_and_all() -> None:
    clue = EmojiClue()
    hints = [lvl.hint for lvl in clue.levels(_params(clue, "profile:emoji:default"))]
    assert hints[0] == {"count": 1}
    assert hints[-1] == {"count": None}


async def test_text_clue_view_has_text_kind_and_accumulated_meta() -> None:
    clue, assets, ref = DescriptionClue(), FakeAssetPort(), asset_ref(6)
    params = _params(clue, "profile:description:default")
    view = await clue.reveal(ref, 2, params, assets=assets, subject="sess:1")
    assert view.kind == "text"
    assert dict(view.meta) == {"fields": ["tagline", "plot_masked"]}
    final = await clue.final_view(ref, params, assets=assets, subject="sess:1")
    assert final.level == 6 and len(final.meta["fields"]) == 7
    assert assets.levels_for("sess:1") == [2, 6]


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (FrameSequenceParams, {"levels": [{"frame": 7, "max_width": 960}]}),
        (FrameSequenceParams, {"levels": [{"frame": 1}]}),
        (FrameSequenceParams, {"levels": [{"frame": i, "max_width": 960} for i in range(1, 8)]}),
        (DescriptionParams, {"levels": [{"fields": []}]}),
        (DescriptionParams, {"levels": [{"fields": ["year", "year"]}]}),
        (DescriptionParams, {"levels": [{"fields": ["budget"]}]}),
        (EmojiParams, {"levels": [{"count": 13}]}),
        (EmojiParams, {"levels": [{"count": "3"}]}),
        (EmojiParams, {"levels": [{}]}),
    ],
)
def test_new_params_are_strict(model: type[BaseModel], body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(body)
