"""Генераторы этапа 1: ``audio_intro``, ``frame_progressive``, ``poster_masked``.

Чистая часть (какие преобразования на каком уровне) вынесена в ``*_ops``/``audio_cuts`` —
её проверяют property-тесты; IO делают порты ``Transcoder`` и ``ImageProcessor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.generators.base import GeneratedAsset
from media_pipeline_service.application.ports import ImageOps, ImageProcessor, Transcoder
from media_pipeline_service.domain.clues import (
    AudioIntroParams,
    ClueParams,
    FrameProgressiveParams,
    PosterMaskedParams,
)
from media_pipeline_service.domain.sources import SourceKind


def _expect[P: ClueParams](params: ClueParams, model: type[P]) -> P:
    if not isinstance(params, model):
        raise TypeError(f"ожидались параметры {model.__name__}, получены {type(params).__name__}")
    return params


@dataclass(frozen=True, slots=True)
class AudioCut:
    """Отрывок уровня: ``[start_s, start_s + duration_s)``."""

    start_s: float
    duration_s: float


def audio_cuts(params: AudioIntroParams, source_duration_ms: int) -> list[AudioCut]:
    """Отрывки по уровням; исходник короче последнего уровня — ошибка (не повторять)."""
    needed_ms = round((params.start_s + params.levels[-1].seconds) * 1000)
    if source_duration_ms < needed_ms:
        raise GenerationError(
            "source.too_short",
            f"исходник {source_duration_ms} мс, нужно {needed_ms} мс",
            retryable=False,
        )
    return [AudioCut(params.start_s, level.seconds) for level in params.levels]


class AudioIntroGenerator:
    """Отрывок с начала трека: на каждый уровень отдельный объект ``[start, start+seconds)``.

    Ранний уровень физически не содержит позднего аудио: декодер читает только свой
    интервал (см. ``Transcoder.cut_audio``), поэтому клиент не достанет из файла уровня N
    звук уровня N+1.
    """

    clue_type: ClassVar[str] = "audio_intro"
    source_kind: ClassVar[SourceKind] = SourceKind.AUDIO
    params_model: ClassVar[type[ClueParams]] = AudioIntroParams

    def __init__(self, transcoder: Transcoder) -> None:
        self._transcoder = transcoder

    async def generate(self, source: bytes, params: ClueParams) -> list[GeneratedAsset]:
        """Нарезать отрывки уровней."""
        p = _expect(params, AudioIntroParams)
        cuts = audio_cuts(p, await self._transcoder.probe_duration_ms(source))
        result: list[GeneratedAsset] = []
        for cut in cuts:
            data = await self._transcoder.cut_audio(
                source, start_s=cut.start_s, duration_s=cut.duration_s
            )
            result.append(
                GeneratedAsset(
                    data=data, mime="audio/mp4", ext="m4a", duration_ms=round(cut.duration_s * 1000)
                )
            )
        return result


async def _render_all(
    processor: ImageProcessor, source: bytes, ops: list[ImageOps]
) -> list[GeneratedAsset]:
    result: list[GeneratedAsset] = []
    for op in ops:
        image = await processor.render(source, op)
        result.append(
            GeneratedAsset(
                data=image.data,
                mime=image.mime,
                ext=image.ext,
                width=image.width,
                height=image.height,
            )
        )
    return result


def frame_ops(params: FrameProgressiveParams) -> list[ImageOps]:
    """Преобразования кадра по уровням: центральный crop, размытие, ширина."""
    return [
        ImageOps(max_width=level.max_width, crop=level.crop, blur_px=level.blur_px)
        for level in params.levels
    ]


class FrameProgressiveGenerator:
    """Кадр из фильма: на ранних уровнях — размытый центральный фрагмент, дальше чётче."""

    clue_type: ClassVar[str] = "frame_progressive"
    source_kind: ClassVar[SourceKind] = SourceKind.FRAME
    params_model: ClassVar[type[ClueParams]] = FrameProgressiveParams

    def __init__(self, processor: ImageProcessor) -> None:
        self._processor = processor

    async def generate(self, source: bytes, params: ClueParams) -> list[GeneratedAsset]:
        """Отрендерить уровни."""
        return await _render_all(
            self._processor, source, frame_ops(_expect(params, FrameProgressiveParams))
        )


def poster_ops(params: PosterMaskedParams, max_width: int) -> list[ImageOps]:
    """Преобразования постера по уровням: маски на всех уровнях, пикселизация убывает."""
    return [
        ImageOps(
            max_width=max_width, pixel_block=level.pixel_block, mask_regions=params.mask_regions
        )
        for level in params.levels
    ]


class PosterMaskedGenerator:
    """Постер с закрытым названием и пикселизацией, которая уменьшается по уровням.

    Ширина результата — настройка сервиса (в контракте параметров её нет).
    """

    clue_type: ClassVar[str] = "poster_masked"
    source_kind: ClassVar[SourceKind] = SourceKind.POSTER
    params_model: ClassVar[type[ClueParams]] = PosterMaskedParams

    def __init__(self, processor: ImageProcessor, *, max_width: int) -> None:
        self._processor = processor
        self._max_width = max_width

    async def generate(self, source: bytes, params: ClueParams) -> list[GeneratedAsset]:
        """Отрендерить уровни."""
        ops = poster_ops(_expect(params, PosterMaskedParams), self._max_width)
        return await _render_all(self._processor, source, ops)
