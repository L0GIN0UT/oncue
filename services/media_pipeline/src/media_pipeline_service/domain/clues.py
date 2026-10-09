"""Параметры уровней по типам загадок (ClueType).

Модели повторяют схемы ``contracts/clues/<clue_type>.params.v1.json`` (D5): ограничения
полей совпадают с контрактом, это проверяет contract-тест. Сверх контракта домен требует
монотонности: каждый следующий уровень раскрывает не меньше предыдущего — иначе «подсказка»
на уровне N+1 оказалась бы хуже, чем на N.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from itertools import pairwise
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX_LEVELS = 20


class ClueParams(BaseModel):
    """База параметров загадки: неизменяемые, без лишних полей, строгие типы как в JSON Schema."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @classmethod
    def from_mapping(cls, params: Mapping[str, Any]) -> Self:
        """Проверить параметры из конфига.

        Через JSON, а не ``model_validate``: строгий режим pydantic в JSON принимает массивы
        для кортежей и целые для дробных полей, но не строки и не ``bool`` — ровно как
        JSON Schema контракта.
        """
        return cls.model_validate_json(json.dumps(params, allow_nan=False))

    @property
    def levels_count(self) -> int:
        """Число уровней."""
        raise NotImplementedError


class AudioIntroLevel(BaseModel):
    """Уровень ``audio_intro``: длина отрывка с начала (от ``start_s``)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    seconds: float = Field(gt=0, le=60)


class AudioIntroParams(ClueParams):
    """Параметры ``audio_intro``: длительность строго растёт по уровням."""

    levels: tuple[AudioIntroLevel, ...] = Field(min_length=1, max_length=_MAX_LEVELS)
    start_s: float = Field(default=0, ge=0)

    @property
    def levels_count(self) -> int:
        """Число уровней."""
        return len(self.levels)

    @model_validator(mode="after")
    def _monotonic(self) -> Self:
        seconds = [level.seconds for level in self.levels]
        if any(b <= a for a, b in pairwise(seconds)):
            raise ValueError("audio_intro: seconds должны строго расти по уровням")
        return self


class FrameLevel(BaseModel):
    """Уровень ``frame_progressive``: размытие, доля центрального кадра, ширина."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    blur_px: float = Field(ge=0, le=200)
    crop: float = Field(gt=0, le=1)
    max_width: int = Field(ge=64, le=4096)


class FrameProgressiveParams(ClueParams):
    """Параметры ``frame_progressive``: размытие не растёт, кадр и ширина не уменьшаются."""

    levels: tuple[FrameLevel, ...] = Field(min_length=1, max_length=_MAX_LEVELS)

    @property
    def levels_count(self) -> int:
        """Число уровней."""
        return len(self.levels)

    @model_validator(mode="after")
    def _monotonic(self) -> Self:
        for prev, cur in pairwise(self.levels):
            if cur.blur_px > prev.blur_px:
                raise ValueError("frame_progressive: blur_px не может расти по уровням")
            if cur.crop < prev.crop or cur.max_width < prev.max_width:
                raise ValueError("frame_progressive: crop и max_width не могут уменьшаться")
        return self


class PosterLevel(BaseModel):
    """Уровень ``poster_masked``: размер блока пикселизации."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pixel_block: int = Field(ge=1, le=256)


class MaskRegion(BaseModel):
    """Закрашиваемая область (доли ширины и высоты) — например, название на постере."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class PosterMaskedParams(ClueParams):
    """Параметры ``poster_masked``: блок пикселизации не растёт по уровням."""

    levels: tuple[PosterLevel, ...] = Field(min_length=1, max_length=_MAX_LEVELS)
    mask_regions: tuple[MaskRegion, ...] = ()

    @property
    def levels_count(self) -> int:
        """Число уровней."""
        return len(self.levels)

    @model_validator(mode="after")
    def _monotonic(self) -> Self:
        blocks = [level.pixel_block for level in self.levels]
        if any(b > a for a, b in pairwise(blocks)):
            raise ValueError("poster_masked: pixel_block не может расти по уровням")
        return self
