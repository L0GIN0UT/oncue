"""Встроенные типы загадок: уровни, подсказки для UI, выдача ассета уровня и ревила."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gv_core.errors import GoneError
from gv_game_kernel import LevelLockedError, LevelSpec
from gv_game_kernel.builtin import (
    AudioIntroClue,
    AudioIntroParams,
    FrameProgressiveClue,
    FrameProgressiveParams,
    PosterMaskedClue,
    PosterMaskedParams,
)
from gv_game_kernel.builtin.scoring import ByLevelParams
from gv_testkit.game import FakeAssetPort, asset_ref

pytestmark = pytest.mark.unit

AUDIO = AudioIntroParams.model_validate(
    {"start_s": 0, "levels": [{"seconds": 0.5}, {"seconds": 1}, {"seconds": 15}]}
)
FRAME = FrameProgressiveParams.model_validate(
    {
        "levels": [
            {"blur_px": 48, "crop": 0.35, "max_width": 960},
            {"blur_px": 0, "crop": 1.0, "max_width": 960},
        ]
    }
)
POSTER = PosterMaskedParams.model_validate(
    {
        "levels": [{"pixel_block": 32}, {"pixel_block": 1}],
        "mask_regions": [{"x": 0, "y": 0.8, "w": 1, "h": 0.2}],
    }
)


def test_audio_levels_expose_seconds() -> None:
    assert AudioIntroClue().levels(AUDIO) == [
        LevelSpec(1, {"seconds": 0.5}),
        LevelSpec(2, {"seconds": 1.0}),
        LevelSpec(3, {"seconds": 15.0}),
    ]


def test_frame_levels_hide_technical_width() -> None:
    levels = FrameProgressiveClue().levels(FRAME)
    assert levels[0] == LevelSpec(1, {"blur_px": 48.0, "crop": 0.35})
    assert all("max_width" not in level.hint for level in levels)


def test_poster_levels() -> None:
    assert PosterMaskedClue().levels(POSTER) == [
        LevelSpec(1, {"pixel_block": 32}),
        LevelSpec(2, {"pixel_block": 1}),
    ]


def test_static_compatibility() -> None:
    assert AudioIntroClue.domains == frozenset({"music"})
    assert FrameProgressiveClue.domains == PosterMaskedClue.domains == frozenset({"film"})
    for clue in (AudioIntroClue, FrameProgressiveClue, PosterMaskedClue):
        assert clue.supported_answer_formats == frozenset({"classic"})


async def test_reveal_links_requested_level() -> None:
    assets, ref = FakeAssetPort(), asset_ref(3)
    view = await AudioIntroClue().reveal(ref, 2, AUDIO, assets=assets, subject="sess:1")
    assert (view.level, view.kind, dict(view.meta)) == (2, "audio", {"seconds": 1.0})
    assert view.asset.level == 2
    assert assets.levels_for("sess:1") == [2]


async def test_final_view_is_last_level() -> None:
    assets, ref = FakeAssetPort(), asset_ref(2)
    view = await FrameProgressiveClue().final_view(ref, FRAME, assets=assets, subject="sess:2")
    assert (view.level, view.kind) == (2, "image")
    assert assets.levels_for("sess:2") == [2]


@pytest.mark.parametrize("level", [0, 4])
async def test_reveal_rejects_missing_level(level: int) -> None:
    assets = FakeAssetPort()
    with pytest.raises(LevelLockedError):
        await AudioIntroClue().reveal(asset_ref(3), level, AUDIO, assets=assets, subject="s")
    assert assets.issued == []


async def test_reveal_propagates_port_errors() -> None:
    assets, ref = FakeAssetPort(), asset_ref(3)
    assets.gone(ref.asset_set_id)
    with pytest.raises(GoneError):
        await AudioIntroClue().reveal(ref, 1, AUDIO, assets=assets, subject="s")


def test_foreign_params_are_rejected() -> None:
    params = ByLevelParams(points=(1,), partial_ratio=0.5)
    with pytest.raises(TypeError, match="AudioIntroParams"):
        AudioIntroClue().levels(params)


@pytest.mark.parametrize(
    "body",
    [
        {"levels": []},
        {"levels": [{"seconds": 0}]},
        {"levels": [{"seconds": "1"}]},
        {"levels": [{"seconds": 1, "extra": 1}]},
        {"levels": [{"seconds": 1}], "start_s": -1},
    ],
)
def test_audio_params_are_strict(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AudioIntroParams.model_validate(body)
