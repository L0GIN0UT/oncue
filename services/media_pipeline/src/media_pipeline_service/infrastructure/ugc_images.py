"""Проверка и перекодирование пользовательских изображений (UGC, ADR 0048).

Безопасность файла (зона повышенной безопасности, ``docs/plans/stage-5.md`` §9.8):

* тип определяется по сигнатуре и декодированию, а не по имени и ``Content-Type`` клиента;
  список форматов - из конфига назначения (JPEG/PNG/WebP), SVG/ICO/GIF и прочее отвергается;
* заголовок проверяется ДО декодирования пикселей: стороны в пределах назначения, поэтому
  «бомба» (маленький файл - огромная картинка) не доходит до ``load``;
* ``verify`` + повторное открытие, ``DecompressionBomb*`` и усечённые файлы - отказ;
* ровно один кадр (анимированные WebP/APNG отвергаются);
* цвет приводится к 8-битному sRGB RGB: 16-битные кадры масштабируются (``v / 257``, без
  обрезки), CMYK и кадры со встроенным ICC-профилем конвертируются через профиль (битый
  профиль - простая конвертация режима);
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
from PIL import Image, ImageCms, UnidentifiedImageError
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


_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
_DEEP_GRAY = {"I;16", "I;16L", "I;16B", "I;16N", "I"}
_ICC_ERRORS = (ImageCms.PyCMSError, OSError, ValueError, TypeError)


def _to_8_bit(image: Image.Image) -> Image.Image:
    """16-битный серый -> 8-битный ``L`` линейным масштабом (65535 -> 255), без обрезки."""
    if image.mode not in _DEEP_GRAY:
        return image
    wide = image if image.mode == "I" else image.convert("I")
    return wide.point(lambda v: v * (1 / 257)).convert("L")


def _to_srgb(image: Image.Image, icc: bytes | None, icc_max_bytes: int) -> Image.Image:
    """8-битный кадр -> sRGB: через встроенный ICC-профиль, иначе простая смена режима.

    Профиль применяется только к ``RGB``/``CMYK``; битый или чужой профиль не причина отказа.
    Профиль больше ``icc_max_bytes`` игнорируется до разбора LittleCMS (поверхность атаки).
    """
    if icc and len(icc) > icc_max_bytes:
        log.info("ugc.icc_ignored", reason="too_large")
        icc = None
    if icc and image.mode in {"RGB", "CMYK"}:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            converted = ImageCms.profileToProfile(
                image, source, _SRGB, outputMode="RGB", renderingIntent=ImageCms.Intent.PERCEPTUAL
            )
            if converted is not None:
                return converted
        except _ICC_ERRORS as exc:
            log.info("ugc.icc_ignored", reason=type(exc).__name__)
    return image.convert("RGB")


def _invalid(reason: str) -> InvalidUgcImageError:
    """Единая ошибка клиенту; причина - только в лог."""
    log.info("ugc.image_rejected", reason=reason)
    return InvalidUgcImageError("файл не является допустимым изображением")


class PillowUgcEncoder:
    """Реализация порта ``UgcImageEncoder``; тяжёлая работа - в пуле потоков."""

    def __init__(
        self, *, webp_quality: int = 80, concurrency: int = 2, icc_max_bytes: int = 1_048_576
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency должен быть положительным")
        self._quality = webp_quality
        self._icc_max_bytes = icc_max_bytes
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
            icc = image.info.get("icc_profile")
            image = PilImageOps.exif_transpose(image)
            image = _to_8_bit(image)
            has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            if has_alpha:
                image = image.convert("RGBA")
            else:
                image = _to_srgb(
                    image, icc if isinstance(icc, bytes) else None, self._icc_max_bytes
                )
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
