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
