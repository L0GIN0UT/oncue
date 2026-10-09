"""Изображения через Pillow: нормализация исходников и рендер уровней в WebP без метаданных.

Безопасность (§8 п.8): тип определяется по содержимому, декодеры ограничены JPEG/PNG/WebP;
число пикселей проверяется по заголовку ДО декодирования (защита от «бомб»); результат
пересобирается из пикселей — EXIF, XMP, ICC и комментарии не переносятся.
"""

from __future__ import annotations

import asyncio
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, UnidentifiedImageError
from PIL import ImageOps as PilImageOps

from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import ImageOps, NormalizedMedia, RenderedImage

_FORMATS = ("JPEG", "PNG", "WEBP")


@dataclass(frozen=True, slots=True)
class _Limits:
    max_pixels: int
    webp_quality: int


class PillowImageProcessor:
    """Нормализация (PNG) и рендер уровней (WebP). Тяжёлая работа — в пуле потоков."""

    def __init__(self, *, max_pixels: int, webp_quality: int = 80) -> None:
        self._limits = _Limits(max_pixels, webp_quality)

    def _check_size(self, image: Image.Image) -> None:
        width, height = image.size
        if width * height > self._limits.max_pixels:
            raise GenerationError("source.too_many_pixels", retryable=False)

    def _open(self, data: bytes) -> Image.Image:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                opened = Image.open(io.BytesIO(data), formats=_FORMATS)
                self._check_size(opened)  # по заголовку, до декодирования пикселей
                opened.load()
        except GenerationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:  # fmt: skip
            raise GenerationError("source.undecodable", retryable=False) from exc
        # Учитываем ориентацию EXIF до того, как метаданные будут отброшены.
        transposed = PilImageOps.exif_transpose(opened)
        return transposed.convert("RGB")

    @staticmethod
    def _clean(image: Image.Image) -> Image.Image:
        """Новый объект только из пикселей: никакой info/exif/icc от исходника."""
        clean = Image.new("RGB", image.size)
        clean.paste(image)
        return clean

    def _normalize_sync(self, data: bytes) -> NormalizedMedia:
        image = self._clean(self._open(data))
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return NormalizedMedia(out.getvalue(), "image/png", "png")

    def _render_sync(self, data: bytes, ops: ImageOps) -> RenderedImage:
        image = self._open(data)
        image = _mask(image, ops)
        image = _center_crop(image, ops.crop)
        if image.width > ops.max_width:
            height = max(1, round(image.height * ops.max_width / image.width))
            image = image.resize((ops.max_width, height), Image.Resampling.LANCZOS)
        if ops.pixel_block > 1:
            image = _pixelate(image, ops.pixel_block)
        if ops.blur_px > 0:
            image = image.filter(ImageFilter.GaussianBlur(radius=ops.blur_px))
        image = self._clean(image)
        out = io.BytesIO()
        image.save(out, format="WEBP", quality=self._limits.webp_quality, method=4)
        return RenderedImage(out.getvalue(), image.width, image.height)

    async def normalize(self, data: bytes) -> NormalizedMedia:
        """Проверить и пересобрать изображение в PNG."""
        return await asyncio.to_thread(self._normalize_sync, data)

    async def render(self, data: bytes, ops: ImageOps) -> RenderedImage:
        """Отрендерить уровень."""
        return await asyncio.to_thread(self._render_sync, data, ops)


def _mask(image: Image.Image, ops: ImageOps) -> Image.Image:
    if not ops.mask_regions:
        return image
    draw = ImageDraw.Draw(image)
    w, h = image.size
    for r in ops.mask_regions:
        box = (
            round(r.x * w),
            round(r.y * h),
            min(w, round((r.x + r.w) * w)),
            min(h, round((r.y + r.h) * h)),
        )
        draw.rectangle(box, fill=(0, 0, 0))
    return image


def _center_crop(image: Image.Image, fraction: float) -> Image.Image:
    if fraction >= 1:
        return image
    w, h = image.size
    cw, ch = max(1, round(w * fraction)), max(1, round(h * fraction))
    left, top = (w - cw) // 2, (h - ch) // 2
    return image.crop((left, top, left + cw, top + ch))


def _pixelate(image: Image.Image, block: int) -> Image.Image:
    w, h = image.size
    small = image.resize((max(1, w // block), max(1, h // block)), Image.Resampling.BOX)
    return small.resize((w, h), Image.Resampling.NEAREST)
