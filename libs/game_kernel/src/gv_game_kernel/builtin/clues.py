"""Встроенные типы загадок: ``audio_intro``, ``frame_progressive``, ``poster_masked``.

Модели параметров повторяют ``contracts/clues/<key>.params.v1.json`` (contract-тест), а
сами параметры совпадают с профилем media-pipeline: по их ``params_hash`` game находит
готовые ассеты (D7). Уровни загадки = попытки игрока (D9).

Все три типа устроены одинаково: список уровней в ``params.levels``, ассет уровня выдаёт
``AssetPort``. Различаются ключом, доменами, способом подачи (``audio``/``image``) и тем,
какие поля уровня видит клиент (``hint_fields``) — это данные класса, а не ветвления.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, Field, StrictFloat, StrictInt

from gv_game_kernel.builtin._base import PluginParams, typed
from gv_game_kernel.errors import LevelLockedError
from gv_game_kernel.plugins import ClueView, LevelSpec
from gv_game_kernel.ports import AssetPort
from gv_game_kernel.refs import AssetRef

CLASSIC_FORMAT = "classic"


# ----------------------------------------------------------------------------- параметры
class AudioIntroLevel(PluginParams):
    """Уровень ``audio_intro``: длина отрывка с начала трека."""

    seconds: Annotated[StrictFloat, Field(gt=0, le=60)]


class AudioIntroParams(PluginParams):
    """Параметры ``audio_intro`` (``contracts/clues/audio_intro.params.v1.json``)."""

    levels: tuple[AudioIntroLevel, ...] = Field(min_length=1, max_length=20)
    start_s: Annotated[StrictFloat, Field(ge=0)] | None = None


class FrameLevel(PluginParams):
    """Уровень ``frame_progressive``: размытие, доля кадра и ширина."""

    blur_px: Annotated[StrictFloat, Field(ge=0, le=200)]
    crop: Annotated[StrictFloat, Field(gt=0, le=1)]
    max_width: Annotated[StrictInt, Field(ge=64, le=4096)]


class FrameProgressiveParams(PluginParams):
    """Параметры ``frame_progressive`` (``contracts/clues/frame_progressive.params.v1.json``)."""

    levels: tuple[FrameLevel, ...] = Field(min_length=1, max_length=20)


class PosterLevel(PluginParams):
    """Уровень ``poster_masked``: размер блока пикселизации."""

    pixel_block: Annotated[StrictInt, Field(ge=1, le=256)]


class MaskRegion(PluginParams):
    """Область постера, закрытая маской (доли ширины и высоты)."""

    x: Annotated[StrictFloat, Field(ge=0, le=1)]
    y: Annotated[StrictFloat, Field(ge=0, le=1)]
    w: Annotated[StrictFloat, Field(gt=0, le=1)]
    h: Annotated[StrictFloat, Field(gt=0, le=1)]


class PosterMaskedParams(PluginParams):
    """Параметры ``poster_masked`` (``contracts/clues/poster_masked.params.v1.json``)."""

    levels: tuple[PosterLevel, ...] = Field(min_length=1, max_length=20)
    mask_regions: tuple[MaskRegion, ...] | None = None


# ----------------------------------------------------------------------------- плагины
class LeveledClue:
    """Загадка из списка уровней ``params.levels`` с ассетом на каждый уровень.

    Наследник задаёт ``key``, ``domains``, ``params_schema``, ``view_kind`` (как клиент
    показывает ассет) и ``hint_fields`` (поля уровня для UI: ``levels[].hint`` и
    ``meta`` выдачи).
    """

    key: ClassVar[str]
    domains: ClassVar[frozenset[str]]
    params_schema: ClassVar[type[BaseModel]]
    supported_answer_formats: ClassVar[frozenset[str]] = frozenset({CLASSIC_FORMAT})
    view_kind: ClassVar[str]
    hint_fields: ClassVar[frozenset[str]]

    def _levels(self, params: BaseModel) -> tuple[BaseModel, ...]:
        levels: tuple[BaseModel, ...] = getattr(typed(params, self.params_schema), "levels")  # noqa: B009 — поле есть у всех params_schema наследников
        return levels

    def _hint(self, level: BaseModel) -> dict[str, Any]:
        # Any: значения подсказки — числа уровня (секунды, размытие), ядро их не разбирает.
        return level.model_dump(include=set(self.hint_fields))

    def levels(self, params: BaseModel) -> list[LevelSpec]:
        """Уровни загадки: номер с 1 и подсказка для UI."""
        return [
            LevelSpec(index=i, hint=self._hint(level))
            for i, level in enumerate(self._levels(params), start=1)
        ]

    async def reveal(
        self, asset: AssetRef, level: int, params: BaseModel, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Ссылка на ассет уровня ``level`` и мета уровня.

        Открыт ли уровень для игрока, проверяет ``ModeEngine`` по состоянию раунда; здесь —
        только что уровень существует.

        Raises:
            LevelLockedError: уровня нет в конфиге.
            GoneError | DependencyUnavailableError | RateLimitedError: ошибки ``AssetPort``.
        """
        levels = self._levels(params)
        if not 1 <= level <= len(levels):
            raise LevelLockedError(
                "такого уровня нет", details={"level": level, "levels_count": len(levels)}
            )
        link = await assets.link(asset, level, subject)
        return ClueView(
            level=level, kind=self.view_kind, asset=link, meta=self._hint(levels[level - 1])
        )

    async def final_view(
        self, asset: AssetRef, params: BaseModel, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Ревил: последний, самый полный уровень."""
        return await self.reveal(
            asset, len(self._levels(params)), params, assets=assets, subject=subject
        )


class AudioIntroClue(LeveledClue):
    """Отрывок с начала трека, длина растёт по уровням (музыка)."""

    key: ClassVar[str] = "audio_intro"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})
    params_schema: ClassVar[type[BaseModel]] = AudioIntroParams
    view_kind: ClassVar[str] = "audio"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"seconds"})


class FrameProgressiveClue(LeveledClue):
    """Кадр из фильма, с каждым уровнем чётче и шире (кино)."""

    key: ClassVar[str] = "frame_progressive"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})
    params_schema: ClassVar[type[BaseModel]] = FrameProgressiveParams
    view_kind: ClassVar[str] = "image"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"blur_px", "crop"})


class PosterMaskedClue(LeveledClue):
    """Постер без названия, пикселизация уменьшается по уровням (кино)."""

    key: ClassVar[str] = "poster_masked"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})
    params_schema: ClassVar[type[BaseModel]] = PosterMaskedParams
    view_kind: ClassVar[str] = "image"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"pixel_block"})
