"""frame_progressive/default: приближения нет, уровни различаются только размытием."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import media_pipeline_service
import pytest
import yaml
from media_pipeline_service.application.generators.plugins import FrameProgressiveGenerator
from media_pipeline_service.domain.clues import FrameProgressiveParams
from media_pipeline_service.infrastructure.images import PillowImageProcessor
from PIL import Image, ImageFilter, ImageStat

pytestmark = pytest.mark.unit

_PROFILES = Path(media_pipeline_service.__file__).parent / "config" / "asset_profiles.yaml"


def _default_params() -> FrameProgressiveParams:
    doc = yaml.safe_load(_PROFILES.read_text(encoding="utf-8"))
    entry = next(
        p
        for p in doc["profiles"]
        if p["clue_type"] == "frame_progressive" and p["profile_key"] == "default"
    )
    return FrameProgressiveParams.from_mapping(entry["params"])


def _test_frame(width: int = 1280, height: int = 720) -> bytes:
    """Кадр с мелкими деталями (шахматка + диагонали): размытие заметно меняет резкость."""
    image = Image.new("RGB", (width, height))
    px = image.load()
    assert px is not None
    for y in range(height):
        for x in range(width):
            v = 255 if ((x // 8) + (y // 8)) % 2 == 0 else 20
            px[x, y] = (v, (x * 255) // width, (y * 255) // height)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _sharpness(data: bytes) -> float:
    """Дисперсия лапласиана: чем резче кадр, тем выше."""
    gray = Image.open(io.BytesIO(data)).convert("L")
    lap = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    return float(ImageStat.Stat(lap).var[0])


def test_default_profile_has_no_zoom() -> None:
    params = _default_params()
    assert [lv.blur_px for lv in params.levels] == [40, 30, 20, 10, 4, 0]
    assert {lv.crop for lv in params.levels} == {1.0}
    assert {lv.max_width for lv in params.levels} == {960}


def test_levels_have_same_size_and_sharpen_monotonically() -> None:
    source = _test_frame()
    generator = FrameProgressiveGenerator(PillowImageProcessor(max_pixels=4_000_000))
    assets = asyncio.run(generator.generate([source], _default_params()))

    sizes = {(a.width, a.height) for a in assets}
    assert sizes == {(960, 540)}  # тот же кадр без обрезки, пропорции исходника
    scores = [_sharpness(a.data) for a in assets]
    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores)


def test_zero_blur_level_keeps_source_proportions() -> None:
    source = _test_frame(960, 400)  # уже не шире max_width: размер не меняется
    generator = FrameProgressiveGenerator(PillowImageProcessor(max_pixels=4_000_000))
    assets = asyncio.run(generator.generate([source], _default_params()))
    assert all((a.width, a.height) == (960, 400) for a in assets)
