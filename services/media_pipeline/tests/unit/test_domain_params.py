"""params_hash и параметры уровней: стабильность хэша и монотонность уровней."""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from media_pipeline_service.domain.clues import (
    AudioIntroParams,
    FrameProgressiveParams,
    PosterMaskedParams,
)
from media_pipeline_service.domain.params import canonical_json, params_hash
from pydantic import ValidationError

pytestmark = pytest.mark.unit

_json_scalars = st.none() | st.booleans() | st.integers(-(10**6), 10**6) | st.text(max_size=8)
_json = st.recursive(
    _json_scalars,
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=6), inner, max_size=4)
    ),
    max_leaves=12,
)


@given(st.dictionaries(st.text(max_size=6), _json, max_size=6), st.randoms())
def test_params_hash_ignores_key_order(params: dict[str, object], rnd: random.Random) -> None:
    items = list(params.items())
    rnd.shuffle(items)
    assert params_hash(dict(items)) == params_hash(params)


@given(st.integers(-(10**9), 10**9))
def test_integral_float_hashes_like_int(value: int) -> None:
    assert params_hash({"x": float(value)}) == params_hash({"x": value})


@given(st.dictionaries(st.text(max_size=6), _json, min_size=1, max_size=6))
def test_params_hash_changes_with_value(params: dict[str, object]) -> None:
    key = next(iter(params))
    changed = {**params, key: ["changed", params[key]]}
    assert params_hash(changed) != params_hash(params)


def test_params_hash_is_sha256_hex_and_canonical_is_compact() -> None:
    assert canonical_json({"b": 1.0, "a": [0.5, "я"]}) == '{"a":[0.5,"я"],"b":1}'
    digest = params_hash({"a": 1})
    assert len(digest) == 64
    assert int(digest, 16) >= 0


def test_params_hash_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        params_hash({"x": float("nan")})


# ---------------------------------------------------------------- монотонность уровней
@given(st.lists(st.floats(0.01, 60), min_size=1, max_size=20, unique=True))
def test_audio_levels_accepted_only_when_strictly_increasing(seconds: list[float]) -> None:
    raw = {"levels": [{"seconds": s} for s in seconds]}
    if seconds == sorted(seconds):
        params = AudioIntroParams.from_mapping(raw)
        assert params.levels_count == len(seconds)
    else:
        with pytest.raises(ValidationError, match="строго расти"):
            AudioIntroParams.from_mapping(raw)


@given(st.lists(st.floats(0, 200), min_size=2, max_size=20))
def test_frame_blur_must_not_increase(blurs: list[float]) -> None:
    raw = {"levels": [{"blur_px": b, "crop": 1, "max_width": 640} for b in blurs]}
    if blurs == sorted(blurs, reverse=True):
        assert FrameProgressiveParams.from_mapping(raw).levels_count == len(blurs)
    else:
        with pytest.raises(ValidationError, match="blur_px"):
            FrameProgressiveParams.from_mapping(raw)


@given(st.lists(st.integers(1, 256), min_size=2, max_size=20))
def test_poster_pixel_block_must_not_increase(blocks: list[int]) -> None:
    raw = {"levels": [{"pixel_block": b} for b in blocks]}
    if blocks == sorted(blocks, reverse=True):
        assert PosterMaskedParams.from_mapping(raw).levels_count == len(blocks)
    else:
        with pytest.raises(ValidationError, match="pixel_block"):
            PosterMaskedParams.from_mapping(raw)


def test_frame_crop_must_not_shrink() -> None:
    with pytest.raises(ValidationError, match="crop"):
        FrameProgressiveParams.from_mapping(
            {
                "levels": [
                    {"blur_px": 10, "crop": 0.8, "max_width": 640},
                    {"blur_px": 5, "crop": 0.5, "max_width": 640},
                ]
            }
        )


@pytest.mark.parametrize(
    "raw",
    [
        {"levels": []},
        {"levels": [{"seconds": 0}]},
        {"levels": [{"seconds": 61}]},
        {"levels": [{"seconds": "1"}]},
        {"levels": [{"seconds": True}]},
        {"levels": [{"seconds": 1}], "extra": 1},
        {"levels": [{"seconds": 1}], "start_s": -1},
    ],
)
def test_audio_params_reject_invalid(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AudioIntroParams.from_mapping(raw)


@settings(max_examples=30)
@given(st.integers(1, 20))
def test_params_hash_is_stable_across_model_roundtrip(n: int) -> None:
    raw = {"levels": [{"pixel_block": 256 - i} for i in range(n)]}
    first = PosterMaskedParams.from_mapping(raw)
    second = PosterMaskedParams.from_mapping(first.model_dump(mode="json"))
    assert params_hash(first.model_dump(mode="json")) == params_hash(second.model_dump(mode="json"))
