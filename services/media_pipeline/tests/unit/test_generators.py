"""Плагины генерации: реестр по ClueType, профили из конфига, монотонность уровней."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.generators import GeneratorRegistry
from media_pipeline_service.application.generators.plugins import (
    AudioIntroGenerator,
    audio_cuts,
    frame_ops,
    poster_ops,
)
from media_pipeline_service.domain.clues import (
    AudioIntroParams,
    FrameProgressiveParams,
    PosterMaskedParams,
)
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.sources import SourceKind

pytestmark = pytest.mark.unit


@st.composite
def frame_params(draw: st.DrawFn) -> FrameProgressiveParams:
    n = draw(st.integers(1, 20))
    blurs = sorted(draw(st.lists(st.floats(0, 200), min_size=n, max_size=n)), reverse=True)
    crops = sorted(draw(st.lists(st.floats(0.01, 1), min_size=n, max_size=n)))
    widths = sorted(draw(st.lists(st.integers(64, 4096), min_size=n, max_size=n)))
    return FrameProgressiveParams.from_mapping(
        {
            "levels": [
                {"blur_px": b, "crop": c, "max_width": w}
                for b, c, w in zip(blurs, crops, widths, strict=True)
            ]
        }
    )


@given(frame_params())
def test_frame_ops_reveal_monotonically(params: FrameProgressiveParams) -> None:
    ops = frame_ops(params)
    assert len(ops) == params.levels_count
    for prev, cur in pairwise(ops):
        assert cur.blur_px <= prev.blur_px
        assert cur.crop >= prev.crop
        assert cur.max_width >= prev.max_width


@given(st.lists(st.integers(1, 256), min_size=1, max_size=20), st.integers(64, 2048))
def test_poster_ops_pixelation_decreases(blocks: list[int], width: int) -> None:
    params = PosterMaskedParams.from_mapping(
        {"levels": [{"pixel_block": b} for b in sorted(blocks, reverse=True)]}
    )
    ops = poster_ops(params, width)
    assert [op.pixel_block for op in ops] == sorted(blocks, reverse=True)
    assert all(op.max_width == width for op in ops)


@given(
    st.lists(st.floats(0.05, 60), min_size=1, max_size=20, unique=True),
    st.floats(0, 30),
)
def test_audio_cuts_start_at_same_point_and_fit_level(seconds: list[float], start: float) -> None:
    params = AudioIntroParams.from_mapping(
        {"start_s": start, "levels": [{"seconds": s} for s in sorted(seconds)]}
    )
    cuts = audio_cuts(params, source_duration_ms=10**9)
    assert len(cuts) == params.levels_count
    for cut, level in zip(cuts, params.levels, strict=True):
        assert cut.start_s == start
        assert cut.duration_s <= level.seconds


def test_audio_cuts_reject_short_source() -> None:
    params = AudioIntroParams.from_mapping({"start_s": 1, "levels": [{"seconds": 2}]})
    assert len(audio_cuts(params, 3000)) == 1
    with pytest.raises(GenerationError) as exc:
        audio_cuts(params, 2999)
    assert (exc.value.code, exc.value.retryable) == ("source.too_short", False)


async def test_generator_rejects_foreign_params(media: Any) -> None:
    generator = AudioIntroGenerator(media.transcoder)
    with pytest.raises(TypeError):
        await generator.generate(
            b"", PosterMaskedParams.from_mapping({"levels": [{"pixel_block": 1}]})
        )


# ---------------------------------------------------------------- реестр и профили
def test_registry_resolves_by_clue_type_without_branches(media: Any) -> None:
    assert media.registry.clue_types == {"audio_intro", "frame_progressive", "poster_masked"}
    assert media.registry.get("audio_intro").source_kind is SourceKind.AUDIO
    assert media.registry.get("unknown") is None
    with pytest.raises(ValueError, match="уже зарегистрирован"):
        media.registry.register(AudioIntroGenerator(media.transcoder))


def test_profiles_by_domain(media: Any) -> None:
    film = {p.clue_type for p in media.profiles.for_domain("film")}
    music = {p.clue_type for p in media.profiles.for_domain("music")}
    assert film == {"frame_progressive", "poster_masked"}
    assert music == {"audio_intro"}


@pytest.mark.parametrize(
    "entry",
    [
        {"clue_type": "nope", "profile_key": "default", "domains": ["film"], "params": {}},
        {"clue_type": "audio_intro", "profile_key": "default", "domains": "music", "params": {}},
        {
            "clue_type": "audio_intro",
            "profile_key": "default",
            "domains": ["music"],
            "params": {"levels": []},
        },
        {
            "clue_type": "audio_intro",
            "profile_key": "default",
            "domains": ["music"],
            "params": {"levels": [{"seconds": 2}, {"seconds": 1}]},
        },
        {
            "clue_type": "audio_intro",
            "profile_key": "Bad",
            "domains": ["music"],
            "params": {"levels": [{"seconds": 1}]},
        },
        {
            "clue_type": "audio_intro",
            "profile_key": "d",
            "domains": ["music"],
            "params": {"levels": [{"seconds": 1}]},
            "x": 1,
        },
    ],
)
def test_registry_rejects_bad_profile(media: Any, entry: dict[str, Any]) -> None:
    with pytest.raises(InvalidProfileError):
        media.registry.build_profile(entry)


@pytest.mark.parametrize(
    "document",
    [
        {"version": 2, "profiles": []},
        {"version": 1, "profiles": []},
        {"version": 1, "profiles": [1]},
    ],
)
def test_registry_rejects_bad_document(document: dict[str, Any]) -> None:
    with pytest.raises(InvalidProfileError):
        GeneratorRegistry().build_catalog(document)


def test_params_for_unknown_clue_type() -> None:
    with pytest.raises(InvalidProfileError):
        GeneratorRegistry().params_for("x", {})
