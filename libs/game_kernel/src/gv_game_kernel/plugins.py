"""Интерфейсы плагинов движка (docs/02-game-engine.md §3, ADR 0008).

Плагин объявляет свой ключ, совместимость и схему параметров; ``EngineFactory`` по
``ModeConfig`` достаёт плагины из ``PluginRegistry`` и проверяет, что набор совместим.
Ядро не ветвится по типу плагина (никаких ``if type == ...``): новый тип загадки, источник
или подсчёт очков — новый класс и регистрация.

Параметры приходят в методы уже разобранными моделью ``params_schema`` плагина.
Реализации встроенных плагинов пишет поток A (``register_builtin``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel

from gv_core.random import Random
from gv_game_kernel.contest import ContestRoundState, PlayerRoundView
from gv_game_kernel.ports import (
    AssetPort,
    CandidatePort,
    ClueAsset,
    CollectionPort,
    PlayableFor,
    PoolQuery,
    SubjectQuery,
)
from gv_game_kernel.refs import AssetRef, MediaItemRef
from gv_game_kernel.round import Guess, MatchVerdict, RoundOutcome, RoundRules
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
        kind: Способ подачи: ``audio`` | ``image`` | ``text`` (клиент выбирает компонент по
            нему; ``text`` — JSON-ассет ``contracts/clues/text_asset.v1.json``).
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
        collections: Порт сборников (источник ``collection``, ADR 0028).
        subject_ids: Персоны, выбранные игроком (марафон) или темой периода (сетка).
        collection_id: Сборник сессии (``POST /v1/sessions.collection_id``); обязателен
            для источника ``collection``, остальные источники его не читают.
        collection_ids: Сборники лобби матча (D12 плана этапа 4: несколько сборников,
            ``source.collection.params.max_collections``). Источник читает
            ``collection_ids`` или, если он пуст, ``collection_id`` как список из одного.
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
    subject_ids: frozenset[UUID] = frozenset()
    collection_id: UUID | None = None
    collection_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class Preset:
    """Пресет-фильтр режима для выбора игроком (``presets[]`` в ``GET /v1/modes``).

    Attributes:
        id: Идентификатор пресета (``preset_id`` в ``POST /v1/sessions``).
        title_key: Ключ i18n названия.
    """

    id: str
    title_key: str


@dataclass(frozen=True, slots=True)
class OptionSpec:
    """Вариант ответа раунда в формате ``pick`` (ADR 0023).

    Клиент видит только ``id`` и подпись; какая единица за вариантом стоит, знает сервер.

    Attributes:
        id: Непрозрачный идентификатор варианта внутри раунда (``a``, ``b``, ``higher``).
        label_key: Ключ i18n подписи (``higher``/``lower``) или ``None``.
        item: Единица за вариантом (сетка — сама единица; «больше/меньше» — ``None``).
    """

    id: str
    label_key: str | None = None
    item: MediaItemRef | None = None


@dataclass(frozen=True, slots=True)
class RoundDraw:
    """Состав раунда, разыгранный источником (ADR 0023).

    Attributes:
        target: Загаданная единица (по ней выдаётся загадка и считается ответ).
        shown: Единицы, которые клиент видит открыто (опорная единица пары).
        options: Варианты ответа формата ``pick`` (пусто — классический ввод).
    """

    target: MediaItemRef
    shown: tuple[MediaItemRef, ...] = ()
    options: tuple[OptionSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class RoundPick:
    """Завершённый раунд для источников с продолжением (сетка) и правил завершения.

    Attributes:
        draw: Состав раунда.
        outcome: Итог раунда.
        option_id: Выбранный вариант (формат ``pick``) или ``None``.
    """

    draw: RoundDraw
    outcome: RoundOutcome
    option_id: str | None = None


class Source(Protocol):
    """Источник единиц раундов (``daily``, ``random``, ``marathon``, ``pair``, ``bracket`` …).

    Attributes:
        key: Ключ в реестре.
        params_schema: Модель параметров (``contracts/modes/params/source.<key>.v1.json``).
        answer_formats: Форматы ответа, для которых источник строит раунды
            (классические — ``classic``; пара и сетка — ``pick``: им нужны варианты).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    answer_formats: ClassVar[frozenset[str]]

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


@runtime_checkable
class RoundSource(Protocol):
    """Источник, который сам составляет раунды (``RoundDraw`` с ``shown``/``options``).

    Классические источники этого не умеют: ``ModeEngine`` оборачивает их единицы в
    ``RoundDraw(target)`` (ADR 0023).
    """

    async def draw_rounds(self, ctx: DrawContext, params: BaseModel, n: int) -> list[RoundDraw]:
        """Начальные раунды сессии (не больше ``n``), детерминированно по ``ctx.seed``.

        Raises:
            PoolTooSmallError: в пуле не хватает единиц даже на один раунд.
        """
        ...

    def compose_rounds(self, params: BaseModel, items: Sequence[MediaItemRef]) -> list[RoundDraw]:
        """Начальные раунды из уже разыгранных единиц (плановый набор периода)."""
        ...


@runtime_checkable
class ProgressiveSource(Protocol):
    """Источник, чьи раунды зависят от прошлых (сетка: пары победителей тура, ADR 0023).

    Раунды материализуются лениво: сервис вызывает ``followups`` после каждого раунда и
    дописывает новые раунды в план сессии.
    """

    def planned_rounds(self, params: BaseModel) -> int:
        """Сколько раундов будет в сессии всего (сетка 32 — 31 выбор)."""
        ...

    def items_needed(self, params: BaseModel) -> int:
        """Сколько единиц разыгрывается на всю сессию (сетка 32 — 32 единицы)."""
        ...

    def followups(self, params: BaseModel, history: Sequence[RoundPick]) -> list[RoundDraw]:
        """Раунды, которые открываются после ``history`` (пусто — пока ничего нового)."""
        ...

    def stage(self, params: BaseModel, index: int) -> int:
        """Этап раунда ``index`` (с 0) для UI — у сетки размер тура: 32, 16 … 2."""
        ...


@runtime_checkable
class SubjectSource(Protocol):
    """Источник по персонам, выбранным игроком (марафон, D14 плана этапа 3a)."""

    def subject_query(
        self, params: BaseModel, *, domain: str, playable_for: PlayableFor | None = None
    ) -> SubjectQuery:
        """Какие персоны можно предложить игроку (роли, минимум играбельных единиц)."""
        ...

    def max_subjects(self, params: BaseModel) -> int:
        """Сколько персон игрок может выбрать за сессию."""
        ...


@runtime_checkable
class PeriodSource(Protocol):
    """Источник плановых наборов периода (дейлик — день, сетка — неделя; ADR 0027)."""

    def period_start(self, params: BaseModel, at: datetime) -> date:
        """Начало периода, в который попадает момент ``at`` (``day`` набора в game)."""
        ...

    def theme_query(
        self, params: BaseModel, *, domain: str, playable_for: PlayableFor | None
    ) -> SubjectQuery | None:
        """Из каких персон генератор набора выбирает тему периода (``None`` — темы нет)."""
        ...


RoundOption = Literal[5, 10, 25, "all"]
"""Вариант числа раундов сессии по сборнику: число или ``"all"`` — весь играбельный пул
(``contracts/modes/params/source.collection.v1.json``)."""


@runtime_checkable
class CollectionBasedSource(Protocol):
    """Источник, который берёт единицы из сборника сессии (``collection``, ADR 0028).

    Конфиг такого режима — шаблон для всех сборников домена: сборник приходит при старте
    (``DrawContext.collection_id``), а число раундов выбирает игрок из ``round_options``
    и ограничивает размер играбельного пула сборника. Раунды у сессий одного режима
    разные, поэтому личные рекорды (``features.records``) у таких режимов запрещены.
    """

    def round_options(self, params: BaseModel) -> list[RoundOption]:
        """Варианты числа раундов, из которых выбирает игрок (в порядке конфига)."""
        ...

    def rounds_for(self, params: BaseModel, requested: RoundOption | None, pool: int) -> int:
        """Число раундов по выбору игрока ``requested`` и размеру пула ``pool``.

        Raises:
            PoolTooSmallError: в пуле меньше ``min_items`` или меньше запрошенного числа.
            RoundsNotAllowedError: запрошенного варианта нет в ``round_options``.
        """
        ...

    async def pool_size(self, ctx: DrawContext, params: BaseModel) -> int:
        """Сколько различных играбельных единиц в сборнике ``ctx.collection_id``.

        Raises:
            ValueError: в контексте нет ``CollectionPort``.
            CollectionRequiredError: в контексте нет ``collection_id``.
        """
        ...


@dataclass(frozen=True, slots=True)
class Comparison:
    """Правило сравнения раунда «больше/меньше» (объявляет источник пары).

    Attributes:
        metric: Метрика каталога (``vote_average`` …): у опорной единицы видна сразу, у
            загаданной — только на ревиле.
        ties: Как судить ничью (``both_correct`` — верен любой вариант).
    """

    metric: str
    ties: str


@runtime_checkable
class ComparisonSource(Protocol):
    """Источник, раунды которого сравнивают единицы по метрике (``pair``)."""

    def comparison(self, params: BaseModel) -> Comparison:
        """Метрика и правило ничьей."""
        ...


# ----------------------------------------------------------------------------- ответы
@dataclass(frozen=True, slots=True)
class AnswerInput:
    """Ответ игрока: выбор из автокомплита, свободный текст или вариант раунда (ровно одно).

    Attributes:
        media_item_id: Выбранная единица.
        text: Свободный текст (1..120 символов, ограничивает API).
        selected: Выбранная единица из проекции game (для ``accept_same_title`` и
            частичных ответов); ``None`` для текста или если единицы нет в проекции.
        option_id: Выбранный вариант раунда (формат ``pick``, ADR 0023).
    """

    media_item_id: UUID | None = None
    text: str | None = None
    selected: MediaItemRef | None = None
    option_id: str | None = None

    @property
    def input_kind(self) -> str:
        """Вид ответа: ``item`` | ``text`` | ``option`` (сверяется с ``AnswerFormat``)."""
        if self.option_id is not None:
            return "option"
        return "item" if self.media_item_id is not None else "text"

    def __post_init__(self) -> None:
        given = [v for v in (self.media_item_id, self.text, self.option_id) if v is not None]
        if len(given) != 1:
            raise ValueError("ответ — ровно одно из media_item_id, text, option_id")
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
        draw: Состав текущего раунда (нужен matcher-ам формата ``pick``).
        comparison: Правило сравнения источника (``metric_compare``) или ``None``.
    """

    domain: str
    normalizer: TitleNormalizer
    partial_rules: tuple[PartialRule, ...] = ()
    draw: RoundDraw | None = None
    comparison: Comparison | None = None


class AnswerMatcher(Protocol):
    """Сверка ответа с загаданной единицей (``title_alias``, ``metric_compare``, ``preference``).

    Attributes:
        key: Ключ в реестре.
        params_schema: Модель параметров (``contracts/modes/params/matcher.<key>.v1.json``).
        answer_formats: Форматы ответа, которые matcher умеет сверять.
        requires_comparison: Нужен источник с правилом сравнения (``ComparisonSource``).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    answer_formats: ClassVar[frozenset[str]]
    requires_comparison: ClassVar[bool]

    def match(
        self, answer: AnswerInput, item: MediaItemRef, params: BaseModel, ctx: MatchContext
    ) -> MatchResult:
        """Точное попадание, частичное (по ``ctx.partial_rules``) или промах."""
        ...


class AnswerFormat(Protocol):
    """Формат ответа (``classic``, ``pick``; ADR 0023): какие ответы принимает раунд.

    Attributes:
        key: Ключ в реестре и значение ``answer.format``.
        input_kinds: Допустимые виды ``AnswerInput.input_kind``.
        needs_options: Нужны ли раунду варианты ответа от источника.
        single_answer: В раунде ровно один ответ (варианты не меняются от уровня к уровню,
            поэтому второй попытки быть не может): загадке нужен один уровень и
            ``attempts_per_level = 1``.
    """

    key: ClassVar[str]
    input_kinds: ClassVar[frozenset[str]]
    needs_options: ClassVar[bool]
    single_answer: ClassVar[bool]


# ----------------------------------------------------------------------------- Scorer
class Scorer(Protocol):
    """Подсчёт очков раунда (``by_level``, ``by_speed``).

    Attributes:
        key: Ключ в реестре.
        params_schema: Модель параметров (``contracts/modes/params/scorer.<key>.v1.json``).
        presentation: Как показывать итог: ``score`` | ``streak`` | ``bracket`` (D12 плана
            этапа 3a; клиент выбирает экран итога по нему).
        requires_early_end: Нужно правило досрочного завершения (``EndRule.ends_early``):
            серия без «жизней» не заканчивается на промахе.
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    presentation: ClassVar[str]
    requires_early_end: ClassVar[bool]

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


# ----------------------------------------------------------------------------- EndRule
class EndRule(Protocol):
    """Правило досрочного завершения сессии (``all_rounds``, ``lives``; ADR 0023).

    Attributes:
        key: Ключ в реестре и значение ``session.end.type``.
        params_schema: Модель параметров (``contracts/modes/params/end.<key>.v1.json``).
        ends_early: Может закончить сессию раньше ``session.rounds`` (по промахам).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    ends_early: ClassVar[bool]

    def should_end(self, history: Sequence[RoundOutcome], params: BaseModel) -> bool:
        """Закончить ли сессию после ``history`` завершённых раундов."""
        ...

    def lives_left(self, history: Sequence[RoundOutcome], params: BaseModel) -> int | None:
        """Сколько «жизней» осталось (для UI) или ``None``, если правило их не считает."""
        ...


# ----------------------------------------------------------------------------- ContestRule
class ContestRule(Protocol):
    """Правило состязания онлайн-матча (``race``, ``parallel``; ADR 0032, D3).

    Ядро не знает видов матча: дуэль и party отличаются только плагином в
    ``session.contest``. Все методы чистые; время — аргумент ``now_ms`` (мс, часы match).

    Attributes:
        key: Ключ в реестре и значение ``session.contest.type``.
        params_schema: Модель параметров (``contracts/modes/params/contest.<key>.v1.json``).
        answer_formats: Совместимые форматы ответа (``race`` — ``classic``;
            ``parallel`` — ``classic``, ``pick``).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    answer_formats: ClassVar[frozenset[str]]

    def round_rules(self, params: BaseModel) -> RoundRules:
        """Правила ходов раунда игрока (попытки на уровень; пропуска и сдачи в матче нет)."""
        ...

    def start_round(
        self, player_ids: Sequence[str], *, now_ms: int, window_ms: int, params: BaseModel
    ) -> ContestRoundState:
        """Начать раунд: открыть уровень 1 для всех игроков с окном ``window_ms``."""
        ...

    def apply_guess(
        self,
        state: ContestRoundState,
        player_id: str,
        guess: Guess,
        *,
        levels_count: int,
        now_ms: int,
        params: BaseModel,
    ) -> ContestRoundState:
        """Применить уже сверенный ход игрока.

        Raises:
            LevelLockedError: уровень закрыт (окно истекло, ``race`` — соперник угадал) или
                у игрока кончились попытки уровня.
            RoundFinishedError: раунд уже завершён.
        """
        ...

    def level_closed(self, state: ContestRoundState, *, now_ms: int, params: BaseModel) -> bool:
        """Закрыт ли текущий уровень: окно истекло, досрочная блокировка или ответили все."""
        ...

    def next_level(
        self,
        state: ContestRoundState,
        *,
        levels_count: int,
        now_ms: int,
        window_ms: int,
        params: BaseModel,
    ) -> ContestRoundState:
        """Открыть следующий уровень после закрытия текущего или завершить раунд."""
        ...

    def round_finished(self, state: ContestRoundState, *, params: BaseModel) -> bool:
        """Раунд окончен для всех (все угадали, уровни кончились, ``race`` — угадано)."""
        ...

    def leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        """Игрок вышел: его раунд ``void``, остальные продолжают."""
        ...

    def outcome(
        self, state: ContestRoundState, player_id: str, *, params: BaseModel
    ) -> RoundOutcome:
        """Итог раунда игрока для ``Scorer`` (``elapsed_ms`` — от открытия уровня ответа)."""
        ...

    def views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        """Статусы игроков для всех участников — без текстов ответов (античит)."""
        ...


# ----------------------------------------------------------------------------- DistractorSource
class DistractorSource(Protocol):
    """Подбор неверных вариантов формата ``pick`` (``lite``; ADR 0040).

    Возможность источника, а не вид режима: ядро не знает, как «близко» подбирать, — это
    плагин в ``answer.params.distractors``. Подбор чистый и детерминированный при заданном
    ``rng`` (план раундов воспроизводим по seed). Пул — играбельные единицы домена, которые
    даёт game; плагин не ходит в каталог сам.

    Attributes:
        key: Ключ в реестре и значение ``answer.params.distractors.type``.
        params_schema: Модель параметров (``contracts/modes/params/distractor.<key>.v1.json``).
    """

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]

    def pick(
        self,
        correct: MediaItemRef,
        pool: Sequence[MediaItemRef],
        *,
        count: int,
        params: BaseModel,
        rng: Random,
    ) -> list[MediaItemRef]:
        """``count`` различных неверных вариантов из ``pool``.

        Верная единица и одноимённые с ней (по ``title_aliases``) в результат не попадают.
        Порядок результата не несёт смысла: перемешивание на комнату — дело match.

        Raises:
            PoolTooSmallError: подходящих кандидатов меньше ``count`` или ``min_pool``.
        """
        ...
