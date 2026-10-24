"""Проверка и перекодирование пользовательских изображений (UGC, ADR 0048).

Безопасность файла (зона повышенной безопасности, ``docs/plans/stage-5.md`` §9.8):

* тип определяется по сигнатуре и декодированию, а не по имени и ``Content-Type`` клиента;
  список форматов - из конфига назначения (JPEG/PNG/WebP), SVG/ICO/GIF и прочее отвергается;
* заголовок проверяется ДО декодирования пикселей: стороны в пределах назначения, поэтому
  «бомба» (маленький файл - огромная картинка) не доходит до ``load``;
* ``verify`` + повторное открытие, ``DecompressionBomb*`` и усечённые файлы - отказ;
* ровно один кадр (анимированные WebP/APNG отвергаются);
* результат пересобирается из пикселей: центр-кроп до квадрата, ресайз, WebP - EXIF, XMP,
  ICC и комментарии не переносятся;
* клиенту детали декодера не раскрываются: любой отказ - ``ugc.invalid_image``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import warnings

import structlog
from PIL import Image, UnidentifiedImageError
from PIL import ImageOps as PilImageOps

from media_pipeline_service.application.ports import EncodedImage
from media_pipeline_service.domain.errors import InvalidUgcImageError
from media_pipeline_service.domain.ugc import UgcPurpose

log = structlog.get_logger(__name__)

# Имена форматов конфига -> форматы Pillow (allowlist декодеров).
_PILLOW_FORMATS = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}

_DECODE_ERRORS = (
    UnidentifiedImageError,
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
    OverflowError,
    TypeError,
    Image.DecompressionBombError,
    Image.DecompressionBombWarning,
)


def _invalid(reason: str) -> InvalidUgcImageError:
    """Единая ошибка клиенту; причина - только в лог."""
    log.info("ugc.image_rejected", reason=reason)
    return InvalidUgcImageError("файл не является допустимым изображением")


class PillowUgcEncoder:
    """Реализация порта ``UgcImageEncoder``; тяжёлая работа - в пуле потоков."""

    def __init__(self, *, webp_quality: int = 80, concurrency: int = 2) -> None:
        if concurrency < 1:
            raise ValueError("concurrency должен быть положительным")
        self._quality = webp_quality
        # Декодирование картинки - CPU и память: одновременно не больше ``concurrency`` файлов,
        # остальные ждут (поток заявок не кладёт процесс).
        self._slots = asyncio.Semaphore(concurrency)

    async def encode(self, data: bytes, purpose: UgcPurpose) -> EncodedImage:
        """Проверить и пересобрать в квадратный WebP без метаданных."""
        async with self._slots:
            return await asyncio.to_thread(self._encode_sync, data, purpose)

    def _open(self, data: bytes, purpose: UgcPurpose) -> Image.Image:
        formats = [_PILLOW_FORMATS[f] for f in purpose.input_formats]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                Image.open(io.BytesIO(data), formats=formats).verify()
                image = Image.open(io.BytesIO(data), formats=formats)
                self._check_header(image, purpose)
                if image.format == "JPEG":
                    # Декодер JPEG сразу уменьшает кадр (масштаб 1/2, 1/4, 1/8): память и время.
                    image.draft("RGB", (purpose.output_side_px, purpose.output_side_px))
                image.load()
        except InvalidUgcImageError:
            raise
        except _DECODE_ERRORS as exc:
            raise _invalid(type(exc).__name__) from exc
        return image

    @staticmethod
    def _check_header(image: Image.Image, purpose: UgcPurpose) -> None:
        if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
            raise _invalid("animated")
        width, height = image.size
        if not (
            purpose.min_side_px <= width <= purpose.max_side_px
            and purpose.min_side_px <= height <= purpose.max_side_px
        ):
            raise _invalid("dimensions")

    def _encode_sync(self, data: bytes, purpose: UgcPurpose) -> EncodedImage:
        image = self._open(data, purpose)
        try:
            image = PilImageOps.exif_transpose(image)
            has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            side = min(image.size)
            left, top = (image.width - side) // 2, (image.height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            out_side = purpose.output_side_px
            image = image.resize((out_side, out_side), Image.Resampling.LANCZOS)
            clean = Image.new(image.mode, image.size)  # только пиксели: info/exif/icc нет
            clean.paste(image)
            out = io.BytesIO()
            clean.save(out, format="WEBP", quality=self._quality, method=4)
        except _DECODE_ERRORS as exc:
            raise _invalid(type(exc).__name__) from exc
        result = out.getvalue()
        return EncodedImage(
            data=result,
            width=clean.width,
            height=clean.height,
            sha256=hashlib.sha256(result).hexdigest(),
        )
