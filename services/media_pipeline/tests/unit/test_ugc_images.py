"""Безопасность файла UGC: проверка по содержимому и перекодирование без метаданных (ADR 0048)."""

from __future__ import annotations

import io

import pytest
from media_pipeline_service.domain.errors import InvalidUgcImageError
from media_pipeline_service.infrastructure.config import load_ugc_catalog
from media_pipeline_service.infrastructure.ugc_images import PillowUgcEncoder
from PIL import Image

pytestmark = pytest.mark.unit

PURPOSE = load_ugc_catalog().purpose("avatar")
ENCODER = PillowUgcEncoder(webp_quality=80)


def _save(image: Image.Image, fmt: str, **kwargs: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def _photo(size: tuple[int, int] = (300, 200), color: tuple[int, int, int] = (200, 30, 30)):  # type: ignore[no-untyped-def]  # Image
    return Image.new("RGB", size, color)


def _exif_jpeg() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "SecretMaker"  # производитель камеры
    exif[0x8825] = {1: "N", 2: (55.0, 45.0, 0.0)}  # GPS-блок
    return _save(_photo(), "JPEG", exif=exif.tobytes())


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
async def test_allowed_formats_become_square_webp(fmt: str) -> None:
    result = await ENCODER.encode(_save(_photo(), fmt), PURPOSE)
    out = Image.open(io.BytesIO(result.data))
    assert out.format == "WEBP"
    assert out.size == (PURPOSE.output_side_px, PURPOSE.output_side_px)
    assert (result.width, result.height) == out.size
    assert len(result.sha256) == 64


async def test_exif_gps_and_icc_are_stripped() -> None:
    source = _exif_jpeg()
    assert b"SecretMaker" in source  # метаданные в исходнике есть
    result = await ENCODER.encode(source, PURPOSE)
    assert b"SecretMaker" not in result.data and b"Exif" not in result.data
    out = Image.open(io.BytesIO(result.data))
    assert not out.getexif() and "icc_profile" not in out.info and "exif" not in out.info


async def test_center_crop_keeps_the_middle() -> None:
    wide = Image.new("RGB", (400, 200), (255, 0, 0))
    wide.paste((0, 0, 255), (150, 0, 250, 200))  # синяя вертикаль по центру
    result = await ENCODER.encode(_save(wide, "PNG"), PURPOSE)
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    side = PURPOSE.output_side_px
    assert out.getpixel((side // 2, side // 2))[2] > 200  # центр синий
    assert out.getpixel((2, side // 2))[0] > 200  # у края красный


async def test_exif_orientation_is_applied_before_cropping() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # повернуть на 90 по часовой
    tall = Image.new("RGB", (300, 150), (255, 0, 0))
    tall.paste((0, 255, 0), (0, 0, 150, 150))  # левая половина зелёная
    result = await ENCODER.encode(_save(tall, "JPEG", exif=exif.tobytes()), PURPOSE)
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    # после поворота левая (зелёная) часть оказывается сверху
    assert out.getpixel((out.width // 2, 5))[1] > out.getpixel((out.width // 2, out.height - 5))[1]


async def test_transparency_is_kept() -> None:
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    result = await ENCODER.encode(_save(image, "PNG"), PURPOSE)
    assert Image.open(io.BytesIO(result.data)).mode == "RGBA"


def _animated_webp() -> bytes:
    frames = [_photo((200, 200), (i * 50, 0, 0)) for i in range(3)]
    return _save(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=50)


def _apng() -> bytes:
    frames = [_photo((200, 200), (i * 50, 0, 0)) for i in range(3)]
    return _save(frames[0], "PNG", save_all=True, append_images=frames[1:], duration=50)


def _gif() -> bytes:
    return _save(_photo((200, 200)), "GIF")


def _ico() -> bytes:
    return _save(_photo((128, 128)), "ICO")


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><script>x</script></svg>'


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(_animated_webp(), id="animated-webp"),
        pytest.param(_apng(), id="apng"),
        pytest.param(_gif(), id="gif"),
        pytest.param(_ico(), id="ico"),
        pytest.param(SVG, id="svg"),
        pytest.param(b"", id="empty"),
        pytest.param(b"not an image at all", id="garbage"),
        pytest.param(b"%PDF-1.7 " + b"0" * 200, id="pdf"),
        pytest.param(_save(_photo(), "PNG")[:-40], id="truncated-png"),
        pytest.param(_save(_photo(), "JPEG")[:-200], id="truncated-jpeg"),
        pytest.param(_save(_photo((64, 64)), "PNG"), id="too-small"),
        pytest.param(_save(_photo((8000, 100)), "PNG"), id="too-wide"),
    ],
)
async def test_invalid_files_are_rejected_with_one_generic_error(data: bytes) -> None:
    with pytest.raises(InvalidUgcImageError) as exc:
        await ENCODER.encode(data, PURPOSE)
    assert exc.value.code == "ugc.invalid_image"
    # детали декодера клиенту не раскрываются
    assert "PIL" not in exc.value.message and "Traceback" not in exc.value.message


async def test_decompression_bomb_is_rejected_by_header_before_decoding() -> None:
    """PNG 30000x30000 сжимается в несколько десятков КБ; пиксели не должны декодироваться."""
    bomb = _save(Image.new("1", (30000, 30000)), "PNG", optimize=False)
    assert len(bomb) < 1_500_000  # проходит лимит по размеру тела
    with pytest.raises(InvalidUgcImageError):
        await ENCODER.encode(bomb, PURPOSE)


async def test_concurrent_encodes_are_limited_and_the_third_waits() -> None:
    """DoS потоком заявок: одновременно декодируется не больше ``concurrency`` файлов."""
    import asyncio
    import threading

    encoder = PillowUgcEncoder(concurrency=2)
    gate = threading.Event()
    lock = threading.Lock()
    state = {"running": 0, "peak": 0, "started": 0}

    def slow(data: bytes, purpose: object) -> object:
        with lock:
            state["running"] += 1
            state["started"] += 1
            state["peak"] = max(state["peak"], state["running"])
        gate.wait(timeout=5)
        with lock:
            state["running"] -= 1
        return object()

    encoder._encode_sync = slow  # type: ignore[method-assign]  # подмена тяжёлой работы
    tasks = [asyncio.create_task(encoder.encode(b"x", PURPOSE)) for _ in range(3)]
    for _ in range(100):
        await asyncio.sleep(0.01)
        if state["started"] >= 2:
            break
    await asyncio.sleep(0.1)
    assert state["started"] == 2  # третий ждёт слот
    gate.set()
    await asyncio.gather(*tasks)
    assert state["started"] == 3 and state["peak"] == 2


def test_concurrency_must_be_positive() -> None:
    with pytest.raises(ValueError):
        PillowUgcEncoder(concurrency=0)


async def test_jpeg_is_downscaled_by_the_decoder_before_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``draft`` до ``load``: большой JPEG не разворачивается в полный размер."""
    from PIL import JpegImagePlugin

    seen: list[tuple[str, tuple[int, int]]] = []
    original = JpegImagePlugin.JpegImageFile.draft

    def spy(self: object, mode: str, size: tuple[int, int]) -> object:
        seen.append((mode, size))
        return original(self, mode, size)  # type: ignore[arg-type]

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    result = await ENCODER.encode(_save(_photo((3000, 2000)), "JPEG"), PURPOSE)

    side = PURPOSE.output_side_px
    assert seen == [("RGB", (side, side))]
    assert (result.width, result.height) == (side, side)


async def test_content_is_decided_by_signature_not_by_extension() -> None:
    """Расширение и MIME клиента кодек не видит: SVG под видом PNG отвергается, PNG - принят."""
    assert (await ENCODER.encode(_save(_photo(), "PNG"), PURPOSE)).width > 0
    with pytest.raises(InvalidUgcImageError):
        await ENCODER.encode(SVG, PURPOSE)


# ---------------------------------------------------------------- глубина и цветовые модели
def _png16(width: int, height: int, color_type: int, pixel: tuple[int, ...]) -> bytes:
    """PNG с 16 битами на канал (Pillow такие RGB не пишет): однотонная заливка."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    row = b"\x00" + b"".join(struct.pack(">H", v) for v in pixel) * width
    header = struct.pack(">IIBBBBB", width, height, 16, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


async def _center_rgb(data: bytes) -> tuple[int, int, int]:
    result = await ENCODER.encode(data, PURPOSE)
    out = Image.open(io.BytesIO(result.data))
    assert out.mode == "RGB"
    side = PURPOSE.output_side_px
    return out.convert("RGB").getpixel((side // 2, side // 2))  # type: ignore[return-value]


def _near(actual: tuple[int, int, int], expected: tuple[int, int, int], tol: int = 12) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (32768, 128), (65535, 255)])
async def test_16_bit_gray_is_scaled_to_8_bit_not_clipped(value: int, expected: int) -> None:
    source = _save(Image.new("I;16", (300, 200), value), "PNG")
    assert Image.open(io.BytesIO(source)).mode == "I;16"
    assert _near(await _center_rgb(source), (expected,) * 3)


async def test_16_bit_gray_gradient_stays_monotonic() -> None:
    gradient = Image.new("I;16", (256, 256))
    gradient.putdata([x * 257 for _ in range(256) for x in range(256)])
    result = await ENCODER.encode(_save(gradient, "PNG"), PURPOSE)
    out = Image.open(io.BytesIO(result.data)).convert("L")
    row = [out.getpixel((x, PURPOSE.output_side_px // 2)) for x in range(0, out.width, 8)]
    assert row == sorted(row)
    assert row[0] < 20 and row[-1] > 235
    assert 100 < row[len(row) // 2] < 156  # середина шкалы - середина, а не белый


async def test_16_bit_rgb_png_keeps_colors() -> None:
    source = _png16(300, 200, 2, (65535, 32768, 0))
    assert _near(await _center_rgb(source), (255, 128, 0))


@pytest.mark.parametrize(
    ("cmyk", "rgb"),
    [
        ((0, 0, 0, 0), (255, 255, 255)),  # бумага
        ((255, 0, 0, 0), (0, 255, 255)),  # cyan
        ((0, 255, 255, 0), (255, 0, 0)),  # красный
        ((0, 0, 0, 255), (0, 0, 0)),  # чёрный K
        ((0, 0, 0, 128), (127, 127, 127)),  # серый K
    ],
)
async def test_cmyk_jpeg_converts_to_rgb_with_right_colors(
    cmyk: tuple[int, int, int, int], rgb: tuple[int, int, int]
) -> None:
    source = _save(Image.new("CMYK", (300, 200), cmyk), "JPEG", quality=95)
    assert Image.open(io.BytesIO(source)).mode == "CMYK"
    assert _near(await _center_rgb(source), rgb, tol=20)


async def test_cmyk_jpeg_keeps_exif_orientation_and_strips_metadata() -> None:
    exif = Image.Exif()
    exif[0x010F] = "SecretMaker"
    exif[0x0112] = 6
    image = Image.new("CMYK", (300, 150), (0, 255, 255, 0))
    source = _save(image, "JPEG", exif=exif.tobytes())
    result = await ENCODER.encode(source, PURPOSE)
    assert b"SecretMaker" not in result.data
    assert (result.width, result.height) == (PURPOSE.output_side_px,) * 2


async def test_embedded_srgb_profile_is_applied_and_not_copied() -> None:
    from PIL import ImageCms

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source = _save(_photo(color=(200, 30, 30)), "JPEG", icc_profile=profile, quality=95)
    assert Image.open(io.BytesIO(source)).info.get("icc_profile")
    result = await ENCODER.encode(source, PURPOSE)
    out = Image.open(io.BytesIO(result.data))
    assert "icc_profile" not in out.info
    assert _near(out.convert("RGB").getpixel((50, 50)), (200, 30, 30))  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["RGB", "CMYK"])
async def test_broken_icc_profile_falls_back_to_plain_conversion(mode: str) -> None:
    color = (200, 30, 30) if mode == "RGB" else (0, 200, 200, 0)
    source = _save(Image.new(mode, (300, 200), color), "JPEG", icc_profile=b"not-a-profile" * 20)
    result = await ENCODER.encode(source, PURPOSE)
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


async def test_oversized_icc_profile_is_ignored_without_cms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Профиль сверх лимита не доходит до LittleCMS: простая смена режима, результат - sRGB."""
    from media_pipeline_service.infrastructure import ugc_images

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("ImageCms не должен вызываться")

    monkeypatch.setattr(ugc_images.ImageCms, "ImageCmsProfile", _boom)
    monkeypatch.setattr(ugc_images.ImageCms, "profileToProfile", _boom)
    source = _save(_photo(color=(10, 200, 30)), "PNG", icc_profile=b"\x00" * 4096)
    encoder = PillowUgcEncoder(webp_quality=80, icc_max_bytes=1024)
    result = await encoder.encode(source, PURPOSE)
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert out.mode == "RGB" and "icc_profile" not in out.info
    r, g, b = out.getpixel((out.width // 2, out.height // 2))
    assert abs(r - 10) < 8 and abs(g - 200) < 8 and abs(b - 30) < 8


async def test_icc_profile_within_limit_still_goes_through_cms() -> None:
    """Профиль в пределах лимита обрабатывается как раньше (битый - без ошибки)."""
    source = _save(_photo(), "PNG", icc_profile=b"\x00" * 64)
    result = await PillowUgcEncoder(icc_max_bytes=1024).encode(source, PURPOSE)
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"
