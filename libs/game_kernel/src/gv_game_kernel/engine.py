"""Движок одного режима — фасад ядра для game (соло) и match (этап 4).

``ModeEngine`` собирает ``EngineFactory.build`` из валидного ``ModeConfig``: параметры
плагинов уже разобраны их ``params_schema``, а сервис работает с готовыми операциями —
уровни, розыгрыш раундов, сверка ответа, переходы раунда, выдача ассета уровня и ревила,
правило завершения сессии, очки. Сам движок без состояния и IO (порты передаются в
вызовы), его можно кэшировать по ``(mode_key, version)``.

Возможности источника (составляет раунды сам, раунды зависят от прошлых, нужны персоны,
сравнение по метрике, плановый период) движок узнаёт по протоколам ``RoundSource``,
``ProgressiveSource``, ``SubjectSource``, ``ComparisonSource``, ``PeriodSource`` — не по
ключу плагина (ADR 0023).

Недоступный ассет (``GoneError`` из ``AssetPort``) движок не перехватывает: game ловит
ошибку и вызывает ``void`` — раунд аннулируется и не входит в ``max_score``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import cached_property
from uuid import UUID

from pydantic import BaseModel

from gv_core.errors import GoneError
from gv_game_kernel.errors import (
    AnswerKindError,
    OptionNotFoundError,
    RoundFinishedError,
    RoundNotFinishedError,
)
from gv_game_kernel.params import params_hash
from gv_game_kernel.plugins import (
    AnswerFormat,
    AnswerInput,
    AnswerMatcher,
    ClueType,
    ClueView,
    Comparison,
    ComparisonSource,
    DrawContext,
    EndRule,
    LevelSpec,
    MatchContext,
    MatchResult,
    OptionSpec,
    PartialRule,
    PeriodSource,
    Preset,
    ProgressiveSource,
    RoundDraw,
    RoundPick,
    RoundSource,
    Scorer,
    Source,
    SubjectSource,
)
from gv_game_kernel.ports import (
    AssetPort,
    CandidatePort,
    CollectionPort,
    PlayableFor,
    PoolQuery,
    SubjectQuery,
)
from gv_game_kernel.refs import AssetRef, MediaItemRef
from gv_game_kernel.round import (
    Guess,
    GuessKind,
    RoundOutcome,
    RoundResult,
    RoundState,
    apply_guess,
    apply_skip,
    give_up,
    require_level_open,
    round_outcome,
    void,
)
from gv_game_kernel.spec import ModeConfig
from gv_text import CONJUNCTION_REPLACEMENTS, NormalizerConfig, TitleNormalizer

DEFAULT_CANDIDATE_LIMIT = 5000
"""Сколько кандидатов запросить у ``CandidatePort``, если game не задал своё значение."""


def answer_normalizer() -> TitleNormalizer:
    """Нормализатор сверки ответов по умолчанию: базовый ``gv_text`` + замены союзов."""
    return TitleNormalizer(NormalizerConfig(replacements=CONJUNCTION_REPLACEMENTS))


@dataclass(frozen=True, slots=True)
class AnswerStep:
    """Итог ответа игрока: новое состояние раунда и результат сверки.

    Attributes:
        state: Состояние после хода.
        match: Вердикт сверки (``partial_kind`` — вид частичного ответа).
        option: Выбранный вариант раунда (формат ``pick``) или ``None``.
    """

    state: RoundState
    match: MatchResult
    option: OptionSpec | None = None


@dataclass(frozen=True, slots=True)
class OptionView:
    """Вариант ответа для клиента (``options[]`` снимка раунда).

    Attributes:
        id: Непрозрачный ``option_id``.
        label_key: Ключ i18n подписи (``game.options.higher``) или ``None``.
        title: Название единицы за вариантом, если источник её показывает (сетка);
            ``None`` — единица скрыта («больше/меньше»).
    """

    id: str
    label_key: str | None
    title: str | None


@dataclass(frozen=True, slots=True)
class ShownView:
    """Открыто показанная единица раунда (``shown[]`` снимка раунда).

    ``item`` — для сервера (найти ассет подачи, название и год), клиенту отдаются только
    ``title``/``year``/``metric`` и подача ассета, без ``media_item_id``.

    Attributes:
        item: Единица.
        option_id: Вариант, который выбирает эту единицу (сетка), или ``None``.
        metric: Видимая метрика (опорная единица «больше/меньше») или ``None``.
    """

    item: MediaItemRef
    option_id: str | None
    metric: float | None


class ModeEngine:
    """Движок режима: плагины конфига с разобранными параметрами.

    Args:
        config: Валидный конфиг режима.
        clue: Тип загадки.
        source: Источник единиц.
        matcher: Сверка ответов.
        partials: Правила частичного ответа (в порядке ``answer.partial``).
        scorer: Подсчёт очков.
        answer_format: Формат ответа (``answer.format``).
        end_rule: Правило завершения сессии (``session.end`` или ``all_rounds``).
        reveal: Тип загадки ревила (``clue.reveal``) или ``None``.
        normalizer: Нормализатор ответов (по умолчанию ``answer_normalizer()``).

    Raises:
        pydantic.ValidationError: параметры не проходят схему плагина (``build`` такой
            конфиг не пропускает — сначала ``EngineFactory.validate``).
    """

    def __init__(
        self,
        config: ModeConfig,
        *,
        clue: ClueType,
        source: Source,
        matcher: AnswerMatcher,
        partials: tuple[PartialRule, ...],
        scorer: Scorer,
        answer_format: AnswerFormat,
        end_rule: EndRule,
        reveal: ClueType | None = None,
        normalizer: TitleNormalizer | None = None,
    ) -> None:
        self.config = config
        self.clue = clue
        self.source = source
        self.matcher = matcher
        self.partials = partials
        self.scorer = scorer
        self.answer_format = answer_format
        self.end_rule = end_rule
        self.reveal = reveal
        end = config.session.end
        self.end_params: BaseModel = end_rule.params_schema.model_validate(
            end.params if end is not None else {}
        )
        reveal_spec = config.clue.reveal
        self.reveal_params: BaseModel | None = (
            reveal.params_schema.model_validate(reveal_spec.params)
            if reveal is not None and reveal_spec is not None
            else None
        )
        self.clue_params: BaseModel = clue.params_schema.model_validate(config.clue.params)
        self.source_params: BaseModel = source.params_schema.model_validate(config.source.params)
        self.matcher_params: BaseModel = matcher.params_schema.model_validate(
            config.answer.matcher.params
        )
        self.scorer_params: BaseModel = scorer.params_schema.model_validate(config.scoring.params)
        self.comparison: Comparison | None = (
            source.comparison(self.source_params) if isinstance(source, ComparisonSource) else None
        )
        self._match_ctx = MatchContext(
            domain=config.domain,
            normalizer=normalizer or answer_normalizer(),
            partial_rules=partials,
            comparison=self.comparison,
        )

    # ------------------------------------------------------------------------- загадка
    def levels(self) -> list[LevelSpec]:
        """Уровни загадки режима (подсказки для UI)."""
        return self.clue.levels(self.clue_params)

    @cached_property
    def levels_count(self) -> int:
        """Число уровней загадки (= число попыток в раунде, D9)."""
        return len(self.levels())

    @cached_property
    def clue_params_hash(self) -> str:
        """``params_hash(clue.params)``: по нему game ищет ассеты из ``AssetsReady`` (D7)."""
        return params_hash(self.config.clue.params)

    @cached_property
    def reveal_params_hash(self) -> str | None:
        """``params_hash(clue.reveal.params)`` или ``None``, если ревил — сама загадка."""
        reveal = self.config.clue.reveal
        return params_hash(reveal.params) if reveal is not None else None

    @property
    def playable_for(self) -> PlayableFor:
        """Требование играбельности единиц под параметры загадки режима."""
        return PlayableFor(clue_type=self.clue.key, params_hash=self.clue_params_hash)

    @property
    def reveal_playable_for(self) -> PlayableFor | None:
        """Второй набор ассетов ревила (ADR 0026) или ``None``, если ревил — сама загадка.

        Единица играбельна, только если готовы оба набора: ``playable_for`` и этот.
        """
        if self.reveal is None or self.reveal_params_hash is None:
            return None
        return PlayableFor(clue_type=self.reveal.key, params_hash=self.reveal_params_hash)

    async def clue_view(
        self, state: RoundState, asset: AssetRef, level: int, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Ассет уровня ``level`` — только если уровень открыт (D11).

        Raises:
            LevelLockedError: уровень выше открытого (403 ``game.level_locked``).
            GoneError | DependencyUnavailableError | RateLimitedError: ошибки ``AssetPort``.
        """
        require_level_open(state, level, levels_count=self.levels_count)
        return await self.clue.reveal(
            asset, level, self.clue_params, assets=assets, subject=subject
        )

    async def final_clue(
        self,
        state: RoundState,
        asset: AssetRef,
        *,
        assets: AssetPort,
        subject: str,
        reveal_asset: AssetRef | None = None,
    ) -> ClueView:
        """Ассет ревила — только у завершённого раунда (D11).

        Args:
            state: Состояние раунда.
            asset: Набор ассетов загадки раунда.
            assets: Порт ссылок.
            subject: Субъект выдачи (``sess:<id>``).
            reveal_asset: Набор ассетов ревила (``reveal_playable_for``) — нужен, если в
                конфиге задан ``clue.reveal``; иначе не используется.

        Raises:
            RoundNotFinishedError: раунд идёт.
            GoneError: ревил другого типа, а его набора нет (как выключенный ассет).
        """
        if not state.finished:
            raise RoundNotFinishedError("ревил доступен только после раунда")
        return await self.final_view(
            asset, assets=assets, subject=subject, reveal_asset=reveal_asset
        )

    async def final_view(
        self,
        asset: AssetRef,
        *,
        assets: AssetPort,
        subject: str,
        reveal_asset: AssetRef | None = None,
    ) -> ClueView:
        """Подача ревила без проверки состояния (раунд заведомо завершён: итог сессии).

        С ``clue.reveal`` — последний уровень загадки ревила по ``reveal_asset``, иначе —
        последний уровень самой загадки по ``asset``.

        Raises:
            GoneError: ревил другого типа, а ``reveal_asset`` не передан.
        """
        if self.reveal is None or self.reveal_params is None:
            return await self.clue.final_view(
                asset, self.clue_params, assets=assets, subject=subject
            )
        if reveal_asset is None:
            raise GoneError("набор ассетов ревила недоступен")
        return await self.reveal.final_view(
            reveal_asset, self.reveal_params, assets=assets, subject=subject
        )

    # ------------------------------------------------------------------------- розыгрыш
    def presets(self) -> list[Preset]:
        """Пресеты источника (пусто — выбора нет)."""
        return self.source.presets(self.source_params)

    def pool_query(self, preset_id: str | None = None) -> PoolQuery | None:
        """Запрос играбельного пула (для проверки ``min_playable`` при публикации).

        Raises:
            PresetNotFoundError: неизвестный пресет.
        """
        return self.source.pool_query(
            self.source_params,
            domain=self.config.domain,
            playable_for=self.playable_for,
            preset_id=preset_id,
        )

    def min_playable(self) -> int:
        """Минимум играбельных единиц пула для публикации конфига."""
        return self.source.min_playable(self.source_params, rounds=self.config.session.rounds)

    def draw_context(
        self,
        *,
        seed: bytes,
        candidates: CandidatePort,
        day: date | None = None,
        preset_id: str | None = None,
        exclude_ids: frozenset[UUID] = frozenset(),
        collections: CollectionPort | None = None,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        subject_ids: frozenset[UUID] = frozenset(),
    ) -> DrawContext:
        """Контекст розыгрыша с полями режима (ключ, домен, загадка, ``params_hash``)."""
        return DrawContext(
            mode_key=self.config.key,
            domain=self.config.domain,
            clue_type=self.clue.key,
            params_hash=self.clue_params_hash,
            seed=seed,
            candidates=candidates,
            day=day,
            preset_id=preset_id,
            exclude_ids=exclude_ids,
            collections=collections,
            candidate_limit=candidate_limit,
            subject_ids=subject_ids,
        )

    def _require_own(self, ctx: DrawContext) -> None:
        if (ctx.mode_key, ctx.clue_type, ctx.params_hash) != (
            self.config.key,
            self.clue.key,
            self.clue_params_hash,
        ):
            raise ValueError("DrawContext собран для другого режима — см. draw_context()")

    @property
    def draw_size(self) -> int:
        """Сколько единиц разыгрывает ``draw`` по умолчанию: ``session.rounds`` или, у
        источника с продолжением, все единицы сессии (сетка 32 — 32)."""
        if isinstance(self.source, ProgressiveSource):
            return self.source.items_needed(self.source_params)
        return self.config.session.rounds

    async def draw(self, ctx: DrawContext, n: int | None = None) -> list[MediaItemRef]:
        """Разыграть ``n`` единиц (по умолчанию ``draw_size``) — набор дня или периода.

        Raises:
            ValueError: контекст собран для другого режима или загадки.
            PoolTooSmallError | PresetNotFoundError: ошибки источника.
        """
        self._require_own(ctx)
        return await self.source.draw(ctx, self.source_params, n or self.draw_size)

    async def draw_rounds(self, ctx: DrawContext, n: int | None = None) -> list[RoundDraw]:
        """Начальные раунды сессии (ADR 0023): не больше ``n`` (по умолчанию
        ``session.rounds``), детерминированно при одном ``ctx.seed`` и пуле.

        Классический источник даёт единицы — каждая становится ``RoundDraw(target)``
        (тот же розыгрыш, что ``draw``). Источник с составом раунда (пара, сетка) отдаёт
        ``shown``/``options`` сам; у сетки это первый тур, следующие — ``followups``.

        Raises:
            ValueError: контекст собран для другого режима или загадки.
            PoolTooSmallError | PresetNotFoundError | SubjectRequiredError |
            TooManySubjectsError: ошибки источника.
        """
        self._require_own(ctx)
        count = n or self.config.session.rounds
        if isinstance(self.source, RoundSource):
            return await self.source.draw_rounds(ctx, self.source_params, count)
        return [
            RoundDraw(target=item)
            for item in await self.source.draw(ctx, self.source_params, count)
        ]

    def compose_rounds(self, items: Sequence[MediaItemRef]) -> list[RoundDraw]:
        """Начальные раунды из уже разыгранного набора (дейлик, неделя сетки)."""
        if isinstance(self.source, RoundSource):
            return self.source.compose_rounds(self.source_params, items)
        return [RoundDraw(target=item) for item in items]

    @property
    def is_progressive(self) -> bool:
        """Раунды зависят от прошлых: после каждого раунда нужен ``followups``."""
        return isinstance(self.source, ProgressiveSource)

    def followups(self, history: Sequence[RoundPick]) -> list[RoundDraw]:
        """Раунды, открытые после ``history`` (все завершённые раунды по порядку).

        Пусто у источника без продолжения и пока текущий тур сетки не доигран.
        """
        if isinstance(self.source, ProgressiveSource):
            return self.source.followups(self.source_params, history)
        return []

    def planned_rounds(self) -> int:
        """Сколько раундов в сессии самое большее: у сетки — ``size - 1``, иначе
        ``session.rounds`` (правило ``session.end`` может закончить раньше)."""
        if isinstance(self.source, ProgressiveSource):
            return self.source.planned_rounds(self.source_params)
        return self.config.session.rounds

    def round_stage(self, index: int) -> int | None:
        """Этап раунда ``index`` для UI (тур сетки: 32, 16 … 2) или ``None``."""
        if isinstance(self.source, ProgressiveSource):
            return self.source.stage(self.source_params, index)
        return None

    def subject_query(self) -> SubjectQuery | None:
        """Какие персоны предлагать игроку перед стартом (марафон) или ``None``."""
        if isinstance(self.source, SubjectSource):
            return self.source.subject_query(
                self.source_params, domain=self.config.domain, playable_for=self.playable_for
            )
        return None

    def max_subjects(self) -> int | None:
        """Сколько персон игрок выбирает (марафон) или ``None`` — выбора нет."""
        if isinstance(self.source, SubjectSource):
            return self.source.max_subjects(self.source_params)
        return None

    def period_start(self, at: datetime) -> date | None:
        """Начало планового периода момента ``at`` (дейлик — день, сетка — неделя) или
        ``None``, если наборов периода у источника нет."""
        if isinstance(self.source, PeriodSource):
            return self.source.period_start(self.source_params, at)
        return None

    def theme_query(self) -> SubjectQuery | None:
        """Из каких персон выбирать тему периода (сетка) или ``None``."""
        if isinstance(self.source, PeriodSource):
            return self.source.theme_query(
                self.source_params, domain=self.config.domain, playable_for=self.playable_for
            )
        return None

    # ------------------------------------------------------------------------- состав раунда
    @staticmethod
    def options_view(draw: RoundDraw) -> list[OptionView]:
        """Варианты раунда для клиента: ``option_id``, подпись и (если единица открыта
        источником) название — без ``media_item_id``."""
        return [
            OptionView(
                id=o.id, label_key=o.label_key, title=o.item.title if o.item is not None else None
            )
            for o in draw.options
        ]

    def shown_view(self, draw: RoundDraw) -> list[ShownView]:
        """Открыто показанные единицы: вариант, который их выбирает, и видимая метрика."""
        by_item = {o.item.id: o.id for o in draw.options if o.item is not None}
        return [
            ShownView(item=item, option_id=by_item.get(item.id), metric=self.metric_of(item))
            for item in draw.shown
        ]

    @property
    def metric_name(self) -> str | None:
        """Метрика сравнения раундов («больше/меньше») или ``None``."""
        return self.comparison.metric if self.comparison is not None else None

    def metric_of(self, item: MediaItemRef) -> float | None:
        """Значение метрики сравнения у единицы (для ``shown`` и ревила) или ``None``."""
        name = self.metric_name
        return item.metric(name) if name is not None else None

    @staticmethod
    def option(draw: RoundDraw, option_id: str) -> OptionSpec:
        """Вариант раунда по ``option_id``.

        Raises:
            OptionNotFoundError: такого варианта в раунде нет (422 ``game.option_unknown``).
        """
        found = next((o for o in draw.options if o.id == option_id), None)
        if found is None:
            raise OptionNotFoundError(
                "такого варианта в раунде нет", details={"option_id": option_id}
            )
        return found

    # ------------------------------------------------------------------------- раунд
    def _require_kind(self, answer: AnswerInput) -> None:
        allowed = self.answer_format.input_kinds
        if answer.input_kind not in allowed:
            raise AnswerKindError(
                "режим не принимает такой ответ",
                details={"kind": answer.input_kind, "allowed": sorted(allowed)},
            )

    def check(
        self, answer: AnswerInput, item: MediaItemRef, draw: RoundDraw | None = None
    ) -> MatchResult:
        """Сверить ответ с загаданной единицей (matcher + правила частичного ответа).

        Args:
            answer: Ответ игрока.
            item: Загаданная единица (``draw.target``).
            draw: Состав раунда — обязателен для ответа ``{option_id}``.

        Raises:
            AnswerKindError: вид ответа не принимается форматом режима
                (422 ``game.answer_kind_unsupported``).
            OptionNotFoundError: ``option_id`` нет среди вариантов раунда
                (422 ``game.option_unknown``).
            ValueError: ответ ``{option_id}`` без состава раунда (ошибка вызова).
        """
        self._require_kind(answer)
        if answer.option_id is not None:
            if draw is None:
                raise ValueError("ответ option_id сверяется только с составом раунда (draw)")
            self.option(draw, answer.option_id)
        ctx = self._match_ctx if draw is None else replace(self._match_ctx, draw=draw)
        return self.matcher.match(answer, item, self.matcher_params, ctx)

    @staticmethod
    def new_round() -> RoundState:
        """Начальное состояние раунда (уровень 1, ходов нет)."""
        return RoundState()

    def advance(self, state: RoundState, guess: Guess) -> RoundState:
        """Применить уже сверенный ответ по правилам сессии (см. ``round.apply_guess``)."""
        return apply_guess(
            state, guess, levels_count=self.levels_count, session=self.config.session
        )

    def answer(
        self,
        state: RoundState,
        answer: AnswerInput,
        item: MediaItemRef,
        *,
        display: str,
        draw: RoundDraw | None = None,
    ) -> AnswerStep:
        """Сверить ответ и применить его к раунду.

        Args:
            state: Состояние раунда (идущего).
            answer: Ответ игрока.
            item: Загаданная единица.
            display: Что показать в истории попыток (название выбранной единицы, текст
                или подпись варианта).
            draw: Состав раунда (обязателен для формата ``pick``).

        Raises:
            RoundFinishedError: раунд уже завершён.
            AnswerKindError | OptionNotFoundError: см. ``check``.
        """
        if state.finished:
            # В завершённом раунде ответ не сверяем: matcher не должен подсказывать ответ.
            raise RoundFinishedError("раунд уже завершён", details={"outcome": str(state.outcome)})
        match = self.check(answer, item, draw)
        option = (
            self.option(draw, answer.option_id)
            if draw is not None and answer.option_id is not None
            else None
        )
        guess = Guess(
            kind=GuessKind(answer.input_kind),
            level=state.level,
            verdict=match.verdict,
            display=display,
        )
        return AnswerStep(state=self.advance(state, guess), match=match, option=option)

    def skip(self, state: RoundState) -> RoundState:
        """Пропустить уровень (``session.skip_allowed``)."""
        return apply_skip(state, levels_count=self.levels_count, session=self.config.session)

    def give_up(self, state: RoundState) -> RoundState:
        """Сдаться (``session.give_up_allowed``)."""
        return give_up(state, session=self.config.session)

    @staticmethod
    def void(state: RoundState) -> RoundState:
        """Аннулировать раунд: ассет недоступен."""
        return void(state)

    def require_level_open(self, state: RoundState, level: int) -> None:
        """Проверить, что уровень можно выдать клиенту (``LevelLockedError`` иначе)."""
        require_level_open(state, level, levels_count=self.levels_count)

    # ------------------------------------------------------------------------- сессия
    def should_end(self, history: Sequence[RoundOutcome]) -> bool:
        """Закончить ли сессию досрочно после ``history`` (правило ``session.end``)."""
        return self.end_rule.should_end(history, self.end_params)

    def lives_left(self, history: Sequence[RoundOutcome]) -> int | None:
        """Сколько «жизней» осталось или ``None``, если правило их не считает."""
        return self.end_rule.lives_left(history, self.end_params)

    @staticmethod
    def streak(history: Sequence[RoundOutcome]) -> int:
        """Текущая серия: сколько последних раундов подряд угаданы точно (``void`` пропускается)."""
        count = 0
        for outcome in reversed(history):
            if outcome.result is RoundResult.VOID:
                continue
            if outcome.result is not RoundResult.EXACT:
                break
            count += 1
        return count

    @property
    def presentation(self) -> str:
        """Подача итога сессии (``Scorer.presentation``): ``score`` | ``streak`` | ``bracket``."""
        return self.scorer.presentation

    # ------------------------------------------------------------------------- очки
    @staticmethod
    def outcome(state: RoundState, *, elapsed_ms: int | None = None) -> RoundOutcome:
        """Итог завершённого раунда для Scorer (см. ``round.round_outcome``)."""
        return round_outcome(state, elapsed_ms=elapsed_ms)

    def score(self, outcome: RoundOutcome) -> int:
        """Очки за раунд."""
        return self.scorer.score(outcome, self.scorer_params)

    def round_max_score(self) -> int:
        """Максимум очков за один раунд."""
        return self.scorer.max_score(self.scorer_params, self.levels_count)

    def max_score(self, rounds: int | None = None) -> int:
        """Максимум очков за ``rounds`` не-``void`` раундов (по умолчанию ``session.rounds``).

        Raises:
            ValueError: отрицательное число раундов.
        """
        count = self.config.session.rounds if rounds is None else rounds
        if count < 0:
            raise ValueError("число раундов не может быть отрицательным")
        return self.round_max_score() * count
