"""Интерфейсы плагинов движка (docs/02-game-engine.md §3, ADR 0008).

Плагин объявляет свой ключ, совместимость и схему параметров; ``EngineFactory`` по
``ModeConfig`` достаёт плагины из ``PluginRegistry`` и проверяет, что набор совместим.
Ядро не ветвится по типу плагина (никаких ``if type == ...``): новый тип загадки, источник
или подсчёт очков — новый класс и регистрация.

Параметры приходят в методы уже разобранными моделью ``params_schema`` плагина.
Реализации встроенных плагинов пишет поток A (``register_builtin``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel

from gv_game_kernel.ports import (
    AssetPort,
    CandidatePort,
    ClueAsset,
    CollectionPort,
    PlayableFor,
    PoolQuery,
)
from gv_game_kernel.refs import AssetRef, MediaItemRef
from gv_game_kernel.round import MatchVerdict, RoundOutcome
from gv_game_kernel.spec import ModeConfig
from gv_text import TitleNormalizer


@dataclass(frozen=True, slots=True)
class ParamIssue:
    """Проблема параметров плагина, найденная перекрёстной проверкой с остальным конфигом.

    Attributes:
        path: Путь внутри ``params`` плагина (``points``, ``size``); ``EngineFactory``
            добавляет префикс секции (``scoring.params.points``).
        code: Машинный код (``config.levels_mismatch``, ``config.rounds_mismatch`` …).
    """

    path: str
    code: str


# ----------------------------------------------------------------------------- ClueType
@dataclass(frozen=True, slots=True)
class LevelSpec:
    """Уровень загадки для UI.

    Attributes:
        index: Номер уровня (с 1).
        hint: Что открывает уровень, например ``{"seconds": 0.5}`` или
            ``{"blur_px": 48, "crop": 0.35}`` (``levels[].hint`` в ``GET /v1/modes``).
    """

    index: int
    # Any: состав подсказки определяет ClueType (числа, строки), ядро её не разбирает.
    hint: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ClueView:
    """Подача уровня загадки клиенту (ответ ``POST .../clue`` и ``final_clue`` ревила).

    Attributes:
        level: Уровень.
        kind: Способ подачи: ``audio`` | ``image`` (клиент выбирает компонент по нему).
        asset: Выданная ссылка на ассет.
        meta: Параметры уровня для UI (``seconds`` или ``blur``).
    """

    level: int
    kind: str
    asset: ClueAsset
    meta: Mapping[str, Any] = field(default_factory=dict)  # Any: см. LevelSpec.hint


class ClueType(Protocol):
    """Тип загадки: уровни и подача ассета уровня.

    Attributes:
        key: Ключ в реестре (``audio_intro``, ``frame_progressive``, ``poster_masked``).
        domains: Домены, где применим.
        params_schema: Модель параметров (повторяет ``contracts/clues/<key>.params.v1.json``).
        supported_answer_formats: Поддерживаемые ``answer.format``.
    """

    key: ClassVar[str]
    domains: ClassVar[frozenset[str]]
    params_schema: ClassVar[type[BaseModel]]
    supported_answer_formats: ClassVar[frozenset[str]]

    def levels(self, params: BaseModel) -> list[LevelSpec]:
        """Уровни загадки по параметрам (число уровней = число попыток, D9)."""
        ...

    async def reveal(
        self, asset: AssetRef, level: int, params: BaseModel, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Подача уровня ``level``: ссылка через ``AssetPort.link`` и мета для UI."""
        ...

    async def final_view(
        self, asset: AssetRef, params: BaseModel, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Подача для ревила завершённого раунда (последний, самый полный уровень)."""
        ...


# ----------------------------------------------------------------------------- Source
@dataclass(frozen=True, slots=True)
class DrawContext:
    """Контекст розыгрыша единиц раундов.

    Attributes:
        mode_key: Ключ режима.
        domain: Домен режима.
        clue_type: Ключ ClueType режима.
        params_hash: ``params_hash(clue.params)`` — играбельны только единицы с ассетами.
        seed: Seed розыгрыша: для дейлика game передаёт ``hmac(secret, mode_key|day)``,
            для случайного режима — seed сессии.
        candidates: Порт кандидатов.
        candidate_limit: Сколько кандидатов запросить у порта (на этапе 2 — весь пул;
            game задаёт значение из настроек).
        day: День набора (дейлик).
        preset_id: Выбранный пресет (случайный режим).
        exclude_ids: Единицы, которых следует избегать (недавние наборы дня): выбираются,
            только если свежих единиц не хватает на ``n``.
        collections: Порт сборников (этап 3; в game на этапе 2 не передаётся).
    """

    mode_key: str
    domain: str
    clue_type: str
    params_hash: str
    seed: bytes
    candidates: CandidatePort
    day: date | None = None
    preset_id: str | None = None
    exclude_ids: frozenset[UUID] = frozenset()
    collections: CollectionPort | None = None
    candidate_limit: int = 5000


@dataclass(frozen=True, slots=True)
class Preset:
    """Пресет-фильтр режима для выбора игроком (``presets[]`` в ``GET /v1/modes``).

    Attributes:
        id: Идентификатор пресета (``preset_id`` в ``POST /v1/sessions``).
        title_key: Ключ i18n названия.
    """

    id: str
    title_key: str


class Source(Protocol):
    """Источник единиц раундов (``daily``, ``random``, ``collection``).

    Attributes:
        key: Ключ в реестре.
        params_schema: Модель параметров (``contracts/modes/params/source.<key>.v1.json``).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """``n`` различных единиц; детерминированно при одинаковых ``ctx.seed`` и пуле.

        Raises:
            PoolTooSmallError: единиц в пуле меньше ``n``.
            PresetNotFoundError: неизвестный ``ctx.preset_id``.
        """
        ...

    def presets(self, params: BaseModel) -> list[Preset]:
        """Пресеты, из которых выбирает игрок (пусто — пресетов нет)."""
        ...

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Согласованность параметров с остальным конфигом (например, ``size == rounds``)."""
        ...

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> PoolQuery | None:
        """Запрос пула: по нему game считает играбельные единицы при публикации конфига.

        Returns:
            ``None``, если источник берёт единицы не из пула (сборник).

        Raises:
            PresetNotFoundError: неизвестный ``preset_id``.
        """
        ...

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """Сколько играбельных единиц должно быть в пуле, чтобы конфиг можно было опубликовать."""
        ...


# ----------------------------------------------------------------------------- ответы
@dataclass(frozen=True, slots=True)
class AnswerInput:
    """Ответ игрока: выбор из автокомплита или свободный текст (ровно одно из двух).

    Attributes:
        media_item_id: Выбранная единица.
        text: Свободный текст (1..120 символов, ограничивает API).
        selected: Выбранная единица из проекции game (для ``accept_same_title`` и
            частичных ответов); ``None`` для текста или если единицы нет в проекции.
    """

    media_item_id: UUID | None = None
    text: str | None = None
    selected: MediaItemRef | None = None

    def __post_init__(self) -> None:
        if (self.media_item_id is None) == (self.text is None):
            raise ValueError("ответ — либо media_item_id, либо text")
        if self.selected is not None and self.selected.id != self.media_item_id:
            raise ValueError("selected должен быть единицей media_item_id")


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Результат сверки ответа.

    Attributes:
        verdict: ``exact`` | ``partial`` | ``miss``.
        partial_kind: Вид частичного ответа (``artist``), если ``verdict = partial``.
    """

    verdict: MatchVerdict
    partial_kind: str | None = None


class PartialRule(Protocol):
    """Правило частичного ответа (``artist``; ``franchise`` — позже).

    Attributes:
        kind: Ключ в реестре и значение ``answer.partial[]``.
        domains: Домены, где применимо.
    """

    kind: ClassVar[str]
    domains: ClassVar[frozenset[str]]

    def check(self, answer: AnswerInput, item: MediaItemRef, ctx: MatchContext) -> bool:
        """Является ли ответ частичным попаданием в ``item``."""
        ...


@dataclass(frozen=True, slots=True)
class MatchContext:
    """Окружение сверки.

    Attributes:
        domain: Домен режима.
        normalizer: Нормализатор ``gv_text`` (тот же, что строит алиасы каталога).
        partial_rules: Правила частичного ответа из ``answer.partial`` (в порядке конфига).
    """

    domain: str
    normalizer: TitleNormalizer
    partial_rules: tuple[PartialRule, ...] = ()


class AnswerMatcher(Protocol):
    """Сверка ответа с загаданной единицей (``title_alias``).

    Attributes:
        key: Ключ в реестре.
        params_schema: Модель параметров (``contracts/modes/params/matcher.<key>.v1.json``).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]

    def match(
        self, answer: AnswerInput, item: MediaItemRef, params: BaseModel, ctx: MatchContext
    ) -> MatchResult:
        """Точное попадание, частичное (по ``ctx.partial_rules``) или промах."""
        ...


# ----------------------------------------------------------------------------- Scorer
class Scorer(Protocol):
    """Подсчёт очков раунда (``by_level``, ``by_speed``).

    Attributes:
        key: Ключ в реестре.
        params_schema: Модель параметров (``contracts/modes/params/scorer.<key>.v1.json``).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int:
        """Очки за раунд (``void`` и ``gave_up`` без частичного — 0)."""
        ...

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        """Максимум очков за ОДИН раунд; максимум сессии = сумма по не-``void`` раундам."""
        ...

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Согласованность с загадкой (например, ``len(points) == levels_count``)."""
        ...
