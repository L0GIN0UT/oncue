"""Встроенные типы загадок.

Этап 2: ``audio_intro``, ``frame_progressive``, ``poster_masked``. Этап 3a:
``audio_reverse`` (музыка), ``soundtrack``, ``audio_blackout``, ``frame_sequence``,
``description`` (кино) и ``emoji`` (музыка и кино).

Модели параметров повторяют ``contracts/clues/<key>.params.v1.json`` (contract-тест), а
сами параметры совпадают с профилем media-pipeline: по их ``params_hash`` game находит
готовые ассеты (D7). Уровни загадки = попытки игрока (D9).

Все типы устроены одинаково: список уровней в ``params.levels``, ассет уровня выдаёт
``AssetPort``. Различаются ключом, доменами, способом подачи (``audio``/``image``/``text``)
и тем, какие поля уровня видит клиент (``hint_fields``; у ``description`` поля
накапливаются) — это данные класса, а не ветвления. Текстовые загадки тоже выдаются
ассетом: JSON по ``contracts/clues/text_asset.v1.json`` (ADR 0024).
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, Field, StrictFloat, StrictInt

from gv_game_kernel.builtin._base import PluginParams, typed, unique
from gv_game_kernel.errors import LevelLockedError
from gv_game_kernel.plugins import ClueView, LevelSpec
from gv_game_kernel.ports import AssetPort
from gv_game_kernel.refs import AssetRef

CLASSIC_FORMAT = "classic"
"""Ключ формата ответа «ввод названия»."""

PICK_FORMAT = "pick"
"""Ключ формата ответа «выбор варианта раунда» (ADR 0023)."""


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


class AudioReverseParams(AudioIntroParams):
    """Параметры ``audio_reverse`` (``contracts/clues/audio_reverse.params.v1.json``).

    Та же форма, что у ``audio_intro``: отрывок с ``start_s`` длиной ``seconds``, но
    проигранный задом наперёд (разворот делает media-pipeline).
    """


class SoundtrackParams(AudioIntroParams):
    """Параметры ``soundtrack`` (``contracts/clues/soundtrack.params.v1.json``)."""


class AudioBlackoutParams(AudioIntroParams):
    """Параметры ``audio_blackout`` (``contracts/clues/audio_blackout.params.v1.json``)."""


class FrameSequenceLevel(PluginParams):
    """Уровень ``frame_sequence``: позиция исходника кадра (1 — самый сложный) и ширина."""

    frame: Annotated[StrictInt, Field(ge=1, le=6)]
    max_width: Annotated[StrictInt, Field(ge=64, le=4096)]


class FrameSequenceParams(PluginParams):
    """Параметры ``frame_sequence`` (``contracts/clues/frame_sequence.params.v1.json``)."""

    levels: tuple[FrameSequenceLevel, ...] = Field(min_length=1, max_length=6)


DescriptionField = Literal[
    "plot_masked", "tagline", "year", "genres", "country", "director", "cast_top3"
]


class DescriptionLevel(PluginParams):
    """Уровень ``description``: поля, которые уровень добавляет к уже открытым."""

    fields: Annotated[
        tuple[DescriptionField, ...],
        Field(min_length=1, max_length=7, json_schema_extra={"uniqueItems": True}),
        AfterValidator(unique),
    ]


class DescriptionParams(PluginParams):
    """Параметры ``description`` (``contracts/clues/description.params.v1.json``)."""

    levels: tuple[DescriptionLevel, ...] = Field(min_length=1, max_length=10)


class EmojiLevel(PluginParams):
    """Уровень ``emoji``: сколько эмодзи открыто (``None`` — все)."""

    count: Annotated[StrictInt, Field(ge=1, le=12)] | None


class EmojiParams(PluginParams):
    """Параметры ``emoji`` (``contracts/clues/emoji.params.v1.json``)."""

    levels: tuple[EmojiLevel, ...] = Field(min_length=1, max_length=12)


# ----------------------------------------------------------------------------- плагины
class LeveledClue:
    """Загадка из списка уровней ``params.levels`` с ассетом на каждый уровень.

    Наследник задаёт ``key``, ``domains``, ``params_schema``, ``view_kind`` (как клиент
    показывает ассет) и ``hint_fields`` (поля уровня для UI: ``levels[].hint`` и
    ``meta`` выдачи). Загадка подходит и для ввода названия, и для выбора варианта:
    формат ``pick`` берёт ассет как подачу показанных единиц (кадр, отрывок).
    """

    key: ClassVar[str]
    domains: ClassVar[frozenset[str]]
    params_schema: ClassVar[type[BaseModel]]
    supported_answer_formats: ClassVar[frozenset[str]] = frozenset({CLASSIC_FORMAT, PICK_FORMAT})
    view_kind: ClassVar[str]
    hint_fields: ClassVar[frozenset[str]]

    def _levels(self, params: BaseModel) -> tuple[BaseModel, ...]:
        levels: tuple[BaseModel, ...] = getattr(typed(params, self.params_schema), "levels")  # noqa: B009 — поле есть у всех params_schema наследников
        return levels

    def _hints(self, levels: tuple[BaseModel, ...]) -> list[dict[str, Any]]:
        """Подсказки уровней по порядку (по умолчанию — поля ``hint_fields`` уровня)."""
        # Any: значения подсказки — числа уровня (секунды, размытие), ядро их не разбирает.
        return [level.model_dump(include=set(self.hint_fields)) for level in levels]

    def levels(self, params: BaseModel) -> list[LevelSpec]:
        """Уровни загадки: номер с 1 и подсказка для UI."""
        hints = self._hints(self._levels(params))
        return [LevelSpec(index=i, hint=hint) for i, hint in enumerate(hints, start=1)]

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
            level=level, kind=self.view_kind, asset=link, meta=self._hints(levels)[level - 1]
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


# ----------------------------------------------------------------------------- этап 3a
class AudioReverseClue(LeveledClue):
    """Отрывок трека задом наперёд, длина растёт по уровням (музыка)."""

    key: ClassVar[str] = "audio_reverse"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})
    params_schema: ClassVar[type[BaseModel]] = AudioReverseParams
    view_kind: ClassVar[str] = "audio"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"seconds"})


class SoundtrackClue(LeveledClue):
    """Отрывок саундтрека фильма, длина растёт по уровням (кино)."""

    key: ClassVar[str] = "soundtrack"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})
    params_schema: ClassVar[type[BaseModel]] = SoundtrackParams
    view_kind: ClassVar[str] = "audio"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"seconds"})


class AudioBlackoutClue(LeveledClue):
    """Звук фильма без картинки (кино); ревил обычно — кадр через ``clue.reveal``."""

    key: ClassVar[str] = "audio_blackout"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})
    params_schema: ClassVar[type[BaseModel]] = AudioBlackoutParams
    view_kind: ClassVar[str] = "audio"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"seconds"})


class FrameSequenceClue(LeveledClue):
    """Разные кадры фильма, от сложного к простому — по одному на уровень (кино)."""

    key: ClassVar[str] = "frame_sequence"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})
    params_schema: ClassVar[type[BaseModel]] = FrameSequenceParams
    view_kind: ClassVar[str] = "image"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"frame"})


class DescriptionClue(LeveledClue):
    """Текстовое описание фильма: каждый уровень добавляет поля к уже открытым (кино).

    Подсказка уровня — все открытые к нему поля в порядке открытия (без повторов): так
    клиент знает, какие блоки будут в JSON-ассете уровня.
    """

    key: ClassVar[str] = "description"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})
    params_schema: ClassVar[type[BaseModel]] = DescriptionParams
    view_kind: ClassVar[str] = "text"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"fields"})

    def _hints(self, levels: tuple[BaseModel, ...]) -> list[dict[str, Any]]:
        """Накопленные поля: подсказка уровня ``i`` — поля уровней ``1..i``."""
        # Any: подсказка — {"fields": [str]}, тип общий с остальными загадками.
        opened: list[str] = []
        hints: list[dict[str, Any]] = []
        for level in levels:
            for name in typed(level, DescriptionLevel).fields:
                if name not in opened:
                    opened.append(name)
            hints.append({"fields": list(opened)})
        return hints


class EmojiClue(LeveledClue):
    """Эмодзи-описание: с каждым уровнем открыто больше эмодзи (музыка и кино)."""

    key: ClassVar[str] = "emoji"
    domains: ClassVar[frozenset[str]] = frozenset({"music", "film"})
    params_schema: ClassVar[type[BaseModel]] = EmojiParams
    view_kind: ClassVar[str] = "text"
    hint_fields: ClassVar[frozenset[str]] = frozenset({"count"})
