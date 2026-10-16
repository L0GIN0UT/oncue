"""Генераторы ассетов по типам загадок.

* этап 1: ``audio_intro``, ``frame_progressive``, ``poster_masked``;
* этап 3a: ``audio_reverse``, ``soundtrack``, ``audio_blackout``, ``frame_sequence``,
  ``description``, ``emoji`` (тексты — JSON по ``contracts/clues/text_asset.v1.json``).

Чистая часть (какие преобразования на каком уровне) вынесена в ``*_ops``/``audio_cuts`` и
``domain.texts`` — её проверяют property-тесты; IO делают порты ``Transcoder`` и
``ImageProcessor``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.generators.base import (
    GeneratedAsset,
    SingleSourceGenerator,
)
from media_pipeline_service.application.ports import ImageOps, ImageProcessor, Transcoder
from media_pipeline_service.domain.clues import (
    AudioBlackoutParams,
    AudioIntroParams,
    AudioReverseParams,
    ClueParams,
    DescriptionParams,
    EmojiParams,
    FrameProgressiveParams,
    FrameSequenceParams,
    PosterMaskedParams,
    SoundtrackParams,
)
from media_pipeline_service.domain.sources import SourceKind, SourceMedia, sources_by_position
from media_pipeline_service.domain.texts import (
    TEXT_ASSET_EXT,
    TEXT_ASSET_MIME,
    description_levels,
    description_supported,
    emoji_levels,
    emoji_supported,
)


def _expect[P: ClueParams](params: ClueParams, model: type[P]) -> P:
    if not isinstance(params, model):
        raise TypeError(f"ожидались параметры {model.__name__}, получены {type(params).__name__}")
    return params


def _single(sources: Sequence[bytes]) -> bytes:
    if len(sources) != 1:
        raise GenerationError("source.count_mismatch", retryable=False)
    return sources[0]


# ---------------------------------------------------------------- аудио
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


class AudioIntroGenerator(SingleSourceGenerator):
    """Отрывок с начала трека: на каждый уровень отдельный объект ``[start, start+seconds)``.

    Ранний уровень физически не содержит позднего аудио: декодер читает только свой
    интервал (см. ``Transcoder.cut_audio``), поэтому клиент не достанет из файла уровня N
    звук уровня N+1. Наследники меняют только данные класса (тип загадки, вид исходника,
    модель параметров) и признак реверса.
    """

    clue_type: ClassVar[str] = "audio_intro"
    source_kind: ClassVar[SourceKind] = SourceKind.AUDIO
    params_model: ClassVar[type[ClueParams]] = AudioIntroParams
    reverse: ClassVar[bool] = False

    def __init__(self, transcoder: Transcoder) -> None:
        self._transcoder = transcoder

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """Нарезать отрывки уровней."""
        _expect(params, self.params_model)
        p = _expect(params, AudioIntroParams)  # все аудио-модели — наследники AudioIntroParams
        source = _single(sources)
        cuts = audio_cuts(p, await self._transcoder.probe_duration_ms(source))
        result: list[GeneratedAsset] = []
        for cut in cuts:
            data = await self._transcoder.cut_audio(
                source, start_s=cut.start_s, duration_s=cut.duration_s, reverse=self.reverse
            )
            result.append(
                GeneratedAsset(
                    data=data, mime="audio/mp4", ext="m4a", duration_ms=round(cut.duration_s * 1000)
                )
            )
        return result


class AudioReverseGenerator(AudioIntroGenerator):
    """Отрывок трека задом наперёд: вырезается ``[start, start+seconds)``, затем реверс.

    Реверс применяется к уже ограниченному входу декодера (``-ss/-t`` до ``-i``): уровень
    не содержит ни одного сэмпла после ``start_s + seconds``.
    """

    clue_type: ClassVar[str] = "audio_reverse"
    params_model: ClassVar[type[ClueParams]] = AudioReverseParams
    reverse: ClassVar[bool] = True


class SoundtrackGenerator(AudioIntroGenerator):
    """Отрывок саундтрека фильма (исходник ``soundtrack``)."""

    clue_type: ClassVar[str] = "soundtrack"
    source_kind: ClassVar[SourceKind] = SourceKind.SOUNDTRACK
    params_model: ClassVar[type[ClueParams]] = SoundtrackParams


class AudioBlackoutGenerator(AudioIntroGenerator):
    """Звук фильма без картинки (исходник ``audio_clip``); ревил — кадром (ADR 0026)."""

    clue_type: ClassVar[str] = "audio_blackout"
    source_kind: ClassVar[SourceKind] = SourceKind.AUDIO_CLIP
    params_model: ClassVar[type[ClueParams]] = AudioBlackoutParams


# ---------------------------------------------------------------- изображения
async def _render(processor: ImageProcessor, source: bytes, op: ImageOps) -> GeneratedAsset:
    image = await processor.render(source, op)
    return GeneratedAsset(
        data=image.data, mime=image.mime, ext=image.ext, width=image.width, height=image.height
    )


async def _render_all(
    processor: ImageProcessor, source: bytes, ops: list[ImageOps]
) -> list[GeneratedAsset]:
    return [await _render(processor, source, op) for op in ops]


def frame_ops(params: FrameProgressiveParams) -> list[ImageOps]:
    """Преобразования кадра по уровням: центральный crop, размытие, ширина."""
    return [
        ImageOps(max_width=level.max_width, crop=level.crop, blur_px=level.blur_px)
        for level in params.levels
    ]


class FrameProgressiveGenerator(SingleSourceGenerator):
    """Кадр из фильма: на ранних уровнях — размытый центральный фрагмент, дальше чётче."""

    clue_type: ClassVar[str] = "frame_progressive"
    source_kind: ClassVar[SourceKind] = SourceKind.FRAME
    params_model: ClassVar[type[ClueParams]] = FrameProgressiveParams

    def __init__(self, processor: ImageProcessor) -> None:
        self._processor = processor

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """Отрендерить уровни."""
        ops = frame_ops(_expect(params, FrameProgressiveParams))
        return await _render_all(self._processor, _single(sources), ops)


def poster_ops(params: PosterMaskedParams, max_width: int) -> list[ImageOps]:
    """Преобразования постера по уровням: маски на всех уровнях, пикселизация убывает."""
    return [
        ImageOps(
            max_width=max_width, pixel_block=level.pixel_block, mask_regions=params.mask_regions
        )
        for level in params.levels
    ]


class PosterMaskedGenerator(SingleSourceGenerator):
    """Постер с закрытым названием и пикселизацией, которая уменьшается по уровням.

    Ширина результата — настройка сервиса (в контракте параметров её нет).
    """

    clue_type: ClassVar[str] = "poster_masked"
    source_kind: ClassVar[SourceKind] = SourceKind.POSTER
    params_model: ClassVar[type[ClueParams]] = PosterMaskedParams

    def __init__(self, processor: ImageProcessor, *, max_width: int) -> None:
        self._processor = processor
        self._max_width = max_width

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """Отрендерить уровни."""
        ops = poster_ops(_expect(params, PosterMaskedParams), self._max_width)
        return await _render_all(self._processor, _single(sources), ops)


class FrameSequenceGenerator:
    """Разные кадры фильма: уровень N — исходник ``frame`` с позицией ``levels[N].frame``.

    Нужно столько кадров, сколько уровней: если у единицы нет кадра на какой-то позиции,
    набор не планируется. Кадр уровня рендерится целиком (без размытия) нужной ширины.
    """

    clue_type: ClassVar[str] = "frame_sequence"
    source_kind: ClassVar[SourceKind] = SourceKind.FRAME
    params_model: ClassVar[type[ClueParams]] = FrameSequenceParams

    def __init__(self, processor: ImageProcessor) -> None:
        self._processor = processor

    def source_count(self, params: ClueParams) -> int:
        """По кадру на уровень."""
        return _expect(params, FrameSequenceParams).levels_count

    def sources_for(
        self, params: ClueParams, sources: Sequence[SourceMedia]
    ) -> list[SourceMedia] | None:
        """Лучший кадр на каждой позиции из уровней, в порядке уровней."""
        by_position = sources_by_position(sources, self.source_kind)
        frames = _expect(params, FrameSequenceParams).frames
        if not all(frame in by_position for frame in frames):
            return None
        return [by_position[frame] for frame in frames]

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """Отрендерить кадр каждого уровня."""
        p = _expect(params, FrameSequenceParams)
        if len(sources) != p.levels_count:
            raise GenerationError("source.count_mismatch", retryable=False)
        return [
            await _render(self._processor, source, ImageOps(max_width=level.max_width))
            for source, level in zip(sources, p.levels, strict=True)
        ]


# ---------------------------------------------------------------- тексты (ADR 0024)
def _json_asset(document: dict[str, Any]) -> GeneratedAsset:
    data = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    return GeneratedAsset(data=data, mime=TEXT_ASSET_MIME, ext=TEXT_ASSET_EXT)


def _text_content(sources: Sequence[bytes]) -> dict[str, Any]:
    try:
        content = json.loads(_single(sources))
    except ValueError as exc:
        raise GenerationError("source.undecodable", retryable=False) from exc
    if not isinstance(content, dict):
        raise GenerationError("source.undecodable", retryable=False)
    return content


class DescriptionGenerator(SingleSourceGenerator):
    """Текстовое описание: каждый уровень показывает все уже открытые поля (``text_asset.v1``).

    Набор планируется, только если у единицы есть все поля, которые открывают уровни.
    """

    clue_type: ClassVar[str] = "description"
    source_kind: ClassVar[SourceKind] = SourceKind.TEXT
    params_model: ClassVar[type[ClueParams]] = DescriptionParams

    def accepts(self, params: ClueParams, source: SourceMedia) -> bool:
        """Есть все поля уровней."""
        content = source.text_content or {}
        return description_supported(content, _expect(params, DescriptionParams))

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """JSON уровней."""
        try:
            levels = description_levels(_text_content(sources), _expect(params, DescriptionParams))
        except ValueError as exc:
            raise GenerationError("text.field_missing", str(exc), retryable=False) from exc
        return [_json_asset(level) for level in levels]


class EmojiGenerator(SingleSourceGenerator):
    """Эмодзи-описание: уровень открывает первые ``count`` графем (``text_asset.v1``)."""

    clue_type: ClassVar[str] = "emoji"
    source_kind: ClassVar[SourceKind] = SourceKind.TEXT
    params_model: ClassVar[type[ClueParams]] = EmojiParams

    def accepts(self, params: ClueParams, source: SourceMedia) -> bool:
        """Эмодзи одобрены и их хватает на явные уровни."""
        return emoji_supported(source.text_content or {}, _expect(params, EmojiParams))

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """JSON уровней."""
        try:
            levels = emoji_levels(_text_content(sources), _expect(params, EmojiParams))
        except ValueError as exc:
            raise GenerationError("text.field_missing", str(exc), retryable=False) from exc
        return [_json_asset(level) for level in levels]
