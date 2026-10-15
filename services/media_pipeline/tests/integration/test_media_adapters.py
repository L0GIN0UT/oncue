"""Адаптеры обработки медиа: ffmpeg (реальный бинарник) и Pillow на синтетических данных."""

from __future__ import annotations

import asyncio
import io
import math
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import ImageOps
from media_pipeline_service.domain.clues import MaskRegion
from media_pipeline_service.infrastructure.ffmpeg import FfmpegTranscoder
from media_pipeline_service.infrastructure.images import PillowImageProcessor
from PIL import Image, ImageDraw

pytestmark = pytest.mark.integration

RATE = 44100
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/usr/bin/ffprobe"
AAC_FRAME_S = 1024 / RATE  # паддинг кодера: тишина, не содержимое


def _ffmpeg(*args: str) -> bytes:
    return subprocess.run(  # noqa: S603 — фиксированные аргументы теста
        [FFMPEG, "-v", "error", "-nostdin", *args], check=True, capture_output=True
    ).stdout


@pytest.fixture(scope="module")
def two_tone_mp3() -> bytes:
    """20 с: первая секунда 440 Гц, дальше 3000 Гц; с тегом title."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "t.mp3"
        _ffmpeg(
            "-f", "lavfi", "-i", "sine=f=440:d=1",
            "-f", "lavfi", "-i", "sine=f=3000:d=19",
            "-filter_complex", "[0][1]concat=n=2:v=0:a=1",
            "-metadata", "title=Interstellar SECRET", "-metadata", "artist=Hans Zimmer",
            str(out),
        )  # fmt: skip
        return out.read_bytes()


def _probe(data: bytes) -> tuple[float, dict[str, str]]:
    with tempfile.NamedTemporaryFile(suffix=".m4a") as f:
        f.write(data)
        f.flush()
        out = subprocess.run(  # noqa: S603
            [
                FFPROBE, "-v", "error", "-show_entries", "format=duration:format_tags",
                "-of", "default=nw=1", f.name,
            ],
            check=True, capture_output=True, text=True,
        ).stdout  # fmt: skip
    duration = 0.0
    tags: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key == "duration":
            duration = float(value)
        elif key.startswith("TAG:"):
            tags[key[4:]] = value
    return duration, tags


def _pcm(data: bytes) -> list[float]:
    with tempfile.NamedTemporaryFile(suffix=".m4a") as f:
        f.write(data)
        f.flush()
        raw = _ffmpeg("-i", f.name, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-")
    return [s / 32768 for (s,) in struct.iter_unpack("<h", raw)]


def _goertzel(samples: list[float], freq: float) -> float:
    k = 2 * math.cos(2 * math.pi * freq / RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return math.sqrt(max(s1 * s1 + s2 * s2 - k * s1 * s2, 0.0)) / max(len(samples), 1)


# ---------------------------------------------------------------- ffmpeg
async def test_cut_levels_respect_duration_and_strip_metadata(two_tone_mp3: bytes) -> None:
    transcoder = FfmpegTranscoder()
    assert abs(await transcoder.probe_duration_ms(two_tone_mp3) - 20_000) < 100
    for seconds in (0.5, 1, 4):
        cut = await transcoder.cut_audio(two_tone_mp3, start_s=0, duration_s=seconds)
        duration, tags = _probe(cut)
        assert duration <= seconds + AAC_FRAME_S + 0.005, (seconds, duration)
        assert duration >= seconds - 0.05
        joined = " ".join(tags.values()).lower()
        assert "secret" not in joined and "zimmer" not in joined
        assert "encoder" not in tags and b"Lavf" not in cut and b"SECRET" not in cut


async def test_early_level_physically_lacks_later_audio(two_tone_mp3: bytes) -> None:
    transcoder = FfmpegTranscoder()
    first = _pcm(await transcoder.cut_audio(two_tone_mp3, start_s=0, duration_s=1))
    later = _pcm(await transcoder.cut_audio(two_tone_mp3, start_s=0, duration_s=4))
    # В уровне 1 нет 3000 Гц (звук после 1-й секунды), в уровне 4 с — есть.
    assert _goertzel(first, 3000) < 0.05 * _goertzel(first, 440)
    assert _goertzel(later, 3000) > _goertzel(first, 3000) * 20


async def test_normalize_produces_flac_without_tags(two_tone_mp3: bytes) -> None:
    result = await FfmpegTranscoder().normalize(two_tone_mp3)
    assert result.data[:4] == b"fLaC"
    assert (result.mime, result.ext) == ("audio/flac", "flac")
    assert b"SECRET" not in result.data and b"Zimmer" not in result.data


@pytest.mark.parametrize(
    "payload",
    [
        b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://169.254.169.254/x.ts\n",
        b"ffconcat version 1.0\nfile '/etc/passwd'\n",
        b"ID3\x04" + b"\0" * 64,
    ],
)
async def test_untrusted_input_is_rejected(payload: bytes) -> None:
    with pytest.raises(GenerationError) as exc:
        await FfmpegTranscoder().normalize(payload)
    assert exc.value.code == "source.undecodable"
    assert not exc.value.retryable


async def test_timeout_kills_process(two_tone_mp3: bytes) -> None:
    transcoder = FfmpegTranscoder(timeout_s=0.001)
    with pytest.raises(GenerationError) as exc:
        await transcoder.normalize(two_tone_mp3)
    assert (exc.value.code, exc.value.retryable) == ("transcode.timeout", True)
    await asyncio.sleep(0)


# ---------------------------------------------------------------- Pillow
def _jpeg_with_exif(width: int = 800, height: int = 600) -> bytes:
    image = Image.new("RGB", (width, height), (30, 60, 90))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 16):  # мелкая сетка: много высоких частот
        draw.line([(x, 0), (x, height)], fill=(250, 250, 250), width=2)
    for y in range(0, height, 16):
        draw.line([(0, y), (width, y)], fill=(200, 20, 20), width=2)
    exif = Image.Exif()
    exif[0x010E] = "SECRET description"  # ImageDescription
    exif[0x8298] = "Copyright Owner"
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes(), comment=b"SECRET comment")
    return out.getvalue()


def _riff_chunks(webp: bytes) -> set[bytes]:
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    chunks, pos = set(), 12
    while pos + 8 <= len(webp):
        name, size = webp[pos : pos + 4], int.from_bytes(webp[pos + 4 : pos + 8], "little")
        chunks.add(name)
        pos += 8 + size + (size & 1)
    return chunks


def _detail(data: bytes) -> float:
    """Средний модуль разности соседних пикселей (энергия высоких частот)."""
    image = Image.open(io.BytesIO(data)).convert("L")
    px = image.tobytes()  # режим "L": один байт на пиксель, построчно
    w, h = image.size
    total = 0
    for y in range(0, h, 2):
        row = y * w
        for x in range(1, w):
            total += abs(px[row + x] - px[row + x - 1])
    return total / (w * h / 2)


def _rgb(image: Image.Image, x: int, y: int) -> tuple[int, ...]:
    """Пиксель RGB-изображения кортежем (``getpixel`` типизирован шире)."""
    pixel = image.getpixel((x, y))
    assert isinstance(pixel, tuple)
    return tuple(int(c) for c in pixel)


@pytest.fixture(scope="module")
def processor() -> PillowImageProcessor:
    return PillowImageProcessor(max_pixels=4_000_000)


async def test_render_is_webp_without_metadata(processor: PillowImageProcessor) -> None:
    rendered = await processor.render(_jpeg_with_exif(), ImageOps(max_width=320, blur_px=4))
    assert (rendered.mime, rendered.ext, rendered.width, rendered.height) == (
        "image/webp", "webp", 320, 240,
    )  # fmt: skip
    assert _riff_chunks(rendered.data).isdisjoint({b"EXIF", b"XMP ", b"ICCP"})
    assert b"SECRET" not in rendered.data and b"Copyright" not in rendered.data


async def test_normalize_strips_metadata(processor: PillowImageProcessor) -> None:
    result = await processor.normalize(_jpeg_with_exif())
    assert result.data.startswith(b"\x89PNG")
    assert b"SECRET" not in result.data and b"eXIf" not in result.data


async def test_center_crop_and_mask(processor: PillowImageProcessor) -> None:
    rendered = await processor.render(_jpeg_with_exif(), ImageOps(max_width=4096, crop=0.5))
    assert (rendered.width, rendered.height) == (400, 300)
    masked = await processor.render(
        _jpeg_with_exif(),
        ImageOps(max_width=800, mask_regions=(MaskRegion(x=0, y=0.5, w=1, h=0.5),)),
    )
    image = Image.open(io.BytesIO(masked.data)).convert("RGB")
    assert max(_rgb(image, 400, 500)) < 20
    assert max(_rgb(image, 400, 100)) > 20


@pytest.mark.parametrize(
    "payload",
    [
        b"GIF89a" + b"\0" * 32,
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"\xff\xd8\xff\xe0broken",
    ],
)
async def test_rejects_non_allowed_or_broken_images(
    processor: PillowImageProcessor, payload: bytes
) -> None:
    with pytest.raises(GenerationError) as exc:
        await processor.normalize(payload)
    assert exc.value.code == "source.undecodable"


async def test_rejects_too_many_pixels_before_decoding() -> None:
    tiny_limit = PillowImageProcessor(max_pixels=10_000)
    with pytest.raises(GenerationError) as exc:
        await tiny_limit.normalize(_jpeg_with_exif(200, 200))
    assert exc.value.code == "source.too_many_pixels"


@settings(max_examples=12, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.function_scoped_fixture])  # fmt: skip
@given(st.integers(0, 30), st.integers(2, 20))
async def test_blur_monotonically_removes_detail(
    processor: PillowImageProcessor, blur: int, delta: int
) -> None:
    source = _jpeg_with_exif(320, 240)
    weak = await processor.render(source, ImageOps(max_width=320, blur_px=blur))
    strong = await processor.render(source, ImageOps(max_width=320, blur_px=blur + delta))
    assert _detail(strong.data) <= _detail(weak.data) + 0.5


async def test_pixelation_detail_grows_as_block_shrinks(processor: PillowImageProcessor) -> None:
    source = _jpeg_with_exif(320, 240)
    details = [
        _detail((await processor.render(source, ImageOps(max_width=320, pixel_block=b))).data)
        for b in (48, 24, 12, 6, 1)
    ]
    assert details == sorted(details), details


async def test_reverse_level_is_reversed_and_lacks_later_audio(two_tone_mp3: bytes) -> None:
    """audio_reverse: реверс ограниченного отрывка — звука после ``start + seconds`` нет."""
    transcoder = FfmpegTranscoder()
    first = _pcm(await transcoder.cut_audio(two_tone_mp3, start_s=0, duration_s=1, reverse=True))
    assert _goertzel(first, 3000) < 0.05 * _goertzel(first, 440)
    # [0.5, 1.5): 440 Гц, затем 3000 Гц; задом наперёд — сначала 3000, потом 440.
    forward = _pcm(await transcoder.cut_audio(two_tone_mp3, start_s=0.5, duration_s=1))
    backward = _pcm(
        await transcoder.cut_audio(two_tone_mp3, start_s=0.5, duration_s=1, reverse=True)
    )
    quarter = RATE // 4
    head, tail = backward[:quarter], backward[-quarter:]
    assert _goertzel(head, 3000) > 5 * _goertzel(head, 440)
    assert _goertzel(tail, 440) > 5 * _goertzel(tail, 3000)
    assert _goertzel(forward[:quarter], 440) > 5 * _goertzel(forward[:quarter], 3000)
