"""Движок одного режима — фасад ядра для game (соло) и match (этап 4).

``ModeEngine`` собирает ``EngineFactory.build`` из валидного ``ModeConfig``: параметры
плагинов уже разобраны их ``params_schema``, а сервис работает с готовыми операциями —
уровни, розыгрыш раундов, сверка ответа, переходы раунда, выдача ассета уровня и ревила,
правило завершения сессии, очки. Сам движок без состояния и IO (порты передаются в
вызовы), его можно кэшировать по ``(mode_key, version)``.

Возможности источника (составляет раунды сам, раунды зависят от прошлых, нужны персоны,
сравнение по метрике, плановый период) движок узнаёт по протоколам ``RoundSource``,
``ProgressiveSource``, ``SubjectSource``, ``ComparisonSource``, ``PeriodSource``,
``CollectionBasedSource`` — не по ключу плагина (ADR 0023, 0028).

Сессия по сборнику (ADR 0028)::

    ctx = engine.draw_context(seed=..., candidates=..., collections=port, collection_id=cid)
    n = await engine.rounds_for(ctx, requested)    # 5 | 10 | 25 | "all" | None
    draws = await engine.draw_rounds(ctx, n)
    max_score = engine.max_score(len(draws))       # по фактическим раундам

Недоступный ассет (``GoneError`` из ``AssetPort``) движок не перехватывает: game ловит
ошибку и вызывает ``void`` — раунд аннулируется и не входит в ``max_score``.

Раунд онлайн-матча (ADR 0032) — фасад ``contest_*`` поверх ``ContestRule`` режима; match
не обращается к плагинам напрямую::

    state = engine.contest_start(player_ids, now_ms=now)          # уровень 1, окно уровня 1
    step = engine.contest_guess(state, pid, answer, draw, display=..., now_ms=now)
    if engine.contest_level_closed(step.state, now_ms=now):        # и по дедлайну окна
        state = engine.contest_advance(step.state, now_ms=now)     # следующий уровень / итог
    if engine.contest_round_finished(state):
        scores = engine.contest_scores(state)                      # очки раунда по игрокам
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import cached_property
from uuid import UUID

from pydantic import BaseModel

from gv_core.errors import GoneError
from gv_core.random import SeededRandom
from gv_game_kernel.contest import ContestRoundState, PlayerRoundView
from gv_game_kernel.errors import (
    AnswerKindError,
    LevelLockedError,
    ModeNotOnlineError,
    ModeNotSoloError,
    OptionNotFoundError,
    RoundFinishedError,
    RoundNotFinishedError,
    RoundsNotAllowedError,
)
from gv_game_kernel.params import params_hash
from gv_game_kernel.plugins import (
    AnswerFormat,
    AnswerInput,
    AnswerMatcher,
    ClueType,
    ClueView,
    CollectionBasedSource,
    Comparison,
    ComparisonSource,
    ContestRule,
    DistractorSource,
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
    RoundOption,
    RoundPick,
    RoundSource,
    ScopedSource,
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
from gv_game_kernel.refs import AssetRef, MediaItemRef, PersonRef
from gv_game_kernel.round import (
    Guess,
    GuessKind,
    RoundOutcome,
    RoundResult,
    RoundRules,
    RoundState,
    apply_guess,
    apply_skip,
    give_up,
    require_level_open,
    round_outcome,
    void,
)
from gv_game_kernel.spec import ModeConfig, OnlineSessionSpec, SoloSessionSpec, solo_end
from gv_text import CONJUNCTION_REPLACEMENTS, NormalizerConfig, TitleNormalizer

DISTRACTOR_SEED_SUFFIX = b"|distractors"
"""Соль seed розыгрыша дистракторов: поток случайности отделён от выбора загаданных единиц."""

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


@dataclass(frozen=True, slots=True)
class ContestStep:
    """Итог хода игрока в матче: новое состояние раунда и результат сверки.

    ``match`` и ``option`` уходят только в ответ команды игрока; другим участникам — только
    статусы ``contest_views`` (античит §12 плана этапа 4).

    Attributes:
        state: Состояние раунда матча после хода.
        match: Вердикт сверки.
        option: Выбранный вариант (формат ``pick``) или ``None``.
    """

    state: ContestRoundState
    match: MatchResult
    option: OptionSpec | None = None


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
        contest: Правило состязания онлайн-сессии (``session.contest``) или ``None``.
        distractors: Источник дистракторов (``answer.params.distractors``) или ``None``.

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
        contest: ContestRule | None = None,
        distractors: DistractorSource | None = None,
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
        self.contest = contest
        self.distractors = distractors
        spec = config.answer.params.distractors if config.answer.params is not None else None
        self.distractor_count: int | None = spec.count if spec is not None else None
        self.distractor_params: BaseModel | None = (
            distractors.params_schema.model_validate(spec.params)
            if distractors is not None and spec is not None
            else None
        )
        session = config.session
        self.contest_params: BaseModel | None = (
            contest.params_schema.model_validate(session.contest.params)
            if contest is not None and isinstance(session, OnlineSessionSpec)
            else None
        )
        end = solo_end(config)
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
        """Минимум играбельных единиц пула для публикации конфига.

        С дистракторами (ADR 0040) — не меньше ``count + 1``: верный вариант и неверные.
        """
        base = self.source.min_playable(self.source_params, rounds=self.config.session.rounds)
        return max(base, (self.distractor_count or 0) + 1)

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
        collection_id: UUID | None = None,
        collection_ids: Sequence[UUID] = (),
    ) -> DrawContext:
        """Контекст розыгрыша с полями режима (ключ, домен, загадка, ``params_hash``).

        ``collection_ids`` — сборники лобби матча (источник ``collection``, D12 этапа 4);
        пустой — источник читает ``collection_id``.
        """
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
            collection_id=collection_id,
            collection_ids=tuple(collection_ids),
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
        ``session.rounds``), детерминированно при одном ``ctx.seed`` и пуле. Для сессии по
        сборнику ``n`` — результат ``rounds_for``.

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
        items = await self.source.draw(ctx, self.source_params, count)
        subjects = await self._subjects(ctx, items)
        draws = [RoundDraw(target=item, subjects=subjects) for item in items]
        return await self._with_distractors(ctx, draws)

    async def _subjects(
        self, ctx: DrawContext, items: Sequence[MediaItemRef]
    ) -> tuple[PersonRef, ...]:
        """Персоны, ограничивающие область ответа (``RoundDraw.subjects``).

        Только при ``answer.subject_mode`` не ``default`` и у источника-``ScopedSource``;
        иначе область не ограничена.
        """
        if self.config.answer.subject_mode == "default" or not isinstance(
            self.source, ScopedSource
        ):
            return ()
        return await self.source.subjects(ctx, self.source_params, items)

    async def _with_distractors(self, ctx: DrawContext, draws: list[RoundDraw]) -> list[RoundDraw]:
        """Варианты ``pick`` раундам без них: верная единица + неверные из пула (ADR 0040).

        Пул — играбельные единицы домена того же вида, что загаданная (один запрос на
        розыгрыш). ``option_id`` — случайные непрозрачные токены, порядок вариантов
        перемешан тем же детерминированным ``rng``: верный не выводится ни из id, ни из
        позиции. Без настроенных дистракторов раунды возвращаются как есть.

        Raises:
            PoolTooSmallError: единиц для вариантов не хватает.
        """
        if (
            self.distractors is None
            or self.distractor_params is None
            or self.distractor_count is None
        ):
            return draws
        # Пул — как у розыгрыша (фильтры пула и пресета ``ctx.preset_id``), но без
        # ``exclude_ids``: память сыгранного избегает повторов загаданных, а не вариантов.
        base = self.pool_query(ctx.preset_id) or PoolQuery(
            domain=ctx.domain, playable_for=self.playable_for
        )
        everyone = await ctx.candidates.candidates(
            replace(base, exclude_ids=frozenset()), ctx.candidate_limit
        )
        rng = SeededRandom(ctx.seed + DISTRACTOR_SEED_SUFFIX)
        result: list[RoundDraw] = []
        for draw in draws:
            pool = [i for i in everyone if i.kind == draw.target.kind]
            wrong = self.distractors.pick(
                draw.target,
                pool,
                count=self.distractor_count,
                params=self.distractor_params,
                rng=rng,
            )
            items = [draw.target, *wrong]
            rng.shuffle(items)
            tokens: set[str] = set()
            options: list[OptionSpec] = []
            for item in items:
                token = f"{rng.randrange(1 << 32):08x}"
                while token in tokens:
                    token = f"{rng.randrange(1 << 32):08x}"
                tokens.add(token)
                options.append(OptionSpec(id=token, item=item))
            result.append(replace(draw, options=tuple(options)))
        return result

    @property
    def uses_collection(self) -> bool:
        """Единицы — из сборника сессии: ``collection_id`` при старте обязателен."""
        return isinstance(self.source, CollectionBasedSource)

    def round_options(self) -> list[RoundOption] | None:
        """Варианты числа раундов для выбора игроком (сборник) или ``None`` — выбора нет."""
        if isinstance(self.source, CollectionBasedSource):
            return self.source.round_options(self.source_params)
        return None

    async def rounds_for(self, ctx: DrawContext, requested: RoundOption | None = None) -> int:
        """Число раундов сессии — его game передаёт в ``draw_rounds`` и ``max_score``.

        У источника из сборника — ``rounds_for`` источника по выбору игрока и размеру
        играбельного пула сборника, но не больше ``session.rounds`` (потолок для
        ``"all"``). У остальных источников выбора нет: ``planned_rounds()``.

        Raises:
            ValueError: контекст собран для другого режима или в нём нет ``CollectionPort``.
            CollectionRequiredError: источнику из сборника не передан ``collection_id``.
            RoundsNotAllowedError: ``requested`` нет в ``round_options`` (или выбора нет).
            PoolTooSmallError: в сборнике меньше ``min_items`` или запрошенного числа.
        """
        self._require_own(ctx)
        if isinstance(self.source, CollectionBasedSource):
            pool = await self.source.pool_size(ctx, self.source_params)
            rounds = self.source.rounds_for(self.source_params, requested, pool)
            return min(rounds, self.config.session.rounds)
        if requested is not None:
            raise RoundsNotAllowedError(
                "у режима нет выбора числа раундов",
                details={"requested": requested, "options": []},
            )
        return self.planned_rounds()

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
        if self.title_only_for(draw):
            ctx = replace(ctx, partial_rules=())
        return self.matcher.match(answer, item, self.matcher_params, ctx)

    def title_only_for(self, draw: RoundDraw | None) -> bool:
        """Засчитывается ли в раунде только название (``answer.subject_mode``, пункт 49).

        ``title_only`` - всегда; ``single_subject`` - если раунд ограничен ровно одной
        персоной (``draw.subjects``); ``default`` - никогда. Ядро не ветвится по ключам
        плагинов: отключается весь набор правил частичного ответа.
        """
        mode = self.config.answer.subject_mode
        if mode == "title_only":
            return True
        return mode == "single_subject" and draw is not None and len(draw.subjects) == 1

    @property
    def solo_session(self) -> SoloSessionSpec:
        """Правила соло-сессии (game играет только соло).

        Raises:
            ModeNotSoloError: режим онлайн — его играет match (ADR 0032).
        """
        session = self.config.session
        if not isinstance(session, SoloSessionSpec):
            raise ModeNotSoloError("режим онлайн: его играет match", details={"kind": session.kind})
        return session

    @property
    def online_session(self) -> OnlineSessionSpec:
        """Правила онлайн-сессии (match играет только онлайн).

        Raises:
            ModeNotOnlineError: режим соло — его играет game (ADR 0032).
        """
        session = self.config.session
        if not isinstance(session, OnlineSessionSpec):
            raise ModeNotOnlineError("режим соло: его играет game", details={"kind": session.kind})
        return session

    @property
    def round_rules(self) -> RoundRules:
        """Правила ходов раунда: соло — ``session``; онлайн — от ``ContestRule`` (ADR 0032)."""
        session = self.config.session
        if isinstance(session, SoloSessionSpec):
            return session
        contest, params = self._contest()
        return contest.round_rules(params)

    @staticmethod
    def new_round() -> RoundState:
        """Начальное состояние раунда (уровень 1, ходов нет)."""
        return RoundState()

    def advance(self, state: RoundState, guess: Guess) -> RoundState:
        """Применить уже сверенный ответ по правилам сессии (см. ``round.apply_guess``)."""
        return apply_guess(state, guess, levels_count=self.levels_count, session=self.round_rules)

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
        return apply_skip(state, levels_count=self.levels_count, session=self.round_rules)

    def give_up(self, state: RoundState) -> RoundState:
        """Сдаться (``session.give_up_allowed``)."""
        return give_up(state, session=self.round_rules)

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

        Считается по фактическим раундам: у сессии по сборнику или марафона их может быть
        меньше ``session.rounds`` — game передаёт число выданных не-``void`` раундов.

        Raises:
            ValueError: отрицательное число раундов.
        """
        count = self.config.session.rounds if rounds is None else rounds
        if count < 0:
            raise ValueError("число раундов не может быть отрицательным")
        return self.round_max_score() * count

    # ------------------------------------------------------------------------- матч (ADR 0032)
    def _contest(self) -> tuple[ContestRule, BaseModel]:
        """Плагин состязания и его параметры.

        Raises:
            ModeNotOnlineError: режим соло.
        """
        self.online_session  # noqa: B018 — проверка вида сессии (бросает ModeNotOnlineError)
        if self.contest is None or self.contest_params is None:
            raise AssertionError("онлайн-движок собран без ContestRule")  # pragma: no cover
        return self.contest, self.contest_params

    def level_window_ms(self, level: int) -> int:
        """Окно уровня ``level`` (с 1) в мс: ``session.level_windows_s[level - 1]``.

        Raises:
            ModeNotOnlineError: режим соло.
            ValueError: уровня нет у загадки.
        """
        windows = self.online_session.level_windows_s
        if not 1 <= level <= len(windows):
            raise ValueError(f"уровня {level} нет: окон {len(windows)}")
        return windows[level - 1] * 1000

    def contest_start(self, player_ids: Sequence[str], *, now_ms: int) -> ContestRoundState:
        """Начать раунд матча: уровень 1 открыт для всех игроков с окном уровня 1.

        Raises:
            ModeNotOnlineError: режим соло.
            ValueError: нет игроков или игроки повторяются.
        """
        contest, params = self._contest()
        return contest.start_round(
            player_ids, now_ms=now_ms, window_ms=self.level_window_ms(1), params=params
        )

    def contest_guess(
        self,
        state: ContestRoundState,
        player_id: str,
        answer: AnswerInput,
        draw: RoundDraw,
        *,
        display: str,
        now_ms: int,
    ) -> ContestStep:
        """Сверить ответ игрока с загаданной единицей раунда и применить ход.

        Вердикт возвращается только вызывающему (ответ команды игрока); если ход не
        принят (уровень закрыт, раунд завершён), вердикт не раскрывается — ошибка.

        Args:
            state: Состояние раунда матча.
            player_id: Игрок (его участие и слот проверяет match).
            answer: Ответ игрока.
            draw: Состав раунда из плана (``target`` — загаданная единица).
            display: Что показать в истории попыток игрока.
            now_ms: Серверное время хода.

        Raises:
            ModeNotOnlineError: режим соло.
            RoundFinishedError: раунд завершён (для всех или для игрока).
            LevelLockedError: уровень закрыт или попытки уровня исчерпаны.
            AnswerKindError | OptionNotFoundError: см. ``check``.
            ValueError: неизвестный игрок.
        """
        contest, params = self._contest()
        match = self.check(answer, draw.target, draw)
        option = self.option(draw, answer.option_id) if answer.option_id is not None else None
        guess = Guess(
            kind=GuessKind(answer.input_kind),
            level=state.level,
            verdict=match.verdict,
            display=display,
        )
        after = contest.apply_guess(
            state, player_id, guess, levels_count=self.levels_count, now_ms=now_ms, params=params
        )
        return ContestStep(state=after, match=match, option=option)

    def contest_skip(
        self, state: ContestRoundState, player_id: str, *, now_ms: int
    ) -> ContestRoundState:
        """Пас игрока на открытом уровне: он ждёт следующего уровня (как исчерпавший попытки).

        Raises:
            ModeNotOnlineError | RoundFinishedError | LevelLockedError | ValueError: как
                у ``contest_guess``.
        """
        contest, params = self._contest()
        skip = Guess(kind=GuessKind.SKIP, level=state.level, verdict=None, display="")
        return contest.apply_guess(
            state, player_id, skip, levels_count=self.levels_count, now_ms=now_ms, params=params
        )

    def contest_level_closed(self, state: ContestRoundState, *, now_ms: int) -> bool:
        """Закрыт ли текущий уровень (окно истекло, блокировка, ответили все)."""
        contest, params = self._contest()
        return contest.level_closed(state, now_ms=now_ms, params=params)

    @staticmethod
    def contest_deadline_ms(state: ContestRoundState) -> int | None:
        """Дедлайн окна открытого уровня (``phase_ends_at`` match) или ``None``."""
        return None if state.finished else state.level_ends_at_ms

    def contest_advance(self, state: ContestRoundState, *, now_ms: int) -> ContestRoundState:
        """После закрытия уровня — открыть следующий (с его окном) или завершить раунд.

        Raises:
            ModeNotOnlineError: режим соло.
            RoundFinishedError: раунд уже завершён.
            ValueError: уровень ещё открыт.
        """
        contest, params = self._contest()
        windows = self.online_session.level_windows_s
        next_level = min(state.level + 1, len(windows))
        return contest.next_level(
            state,
            levels_count=self.levels_count,
            now_ms=now_ms,
            window_ms=self.level_window_ms(next_level),
            params=params,
        )

    def contest_round_finished(self, state: ContestRoundState) -> bool:
        """Раунд матча завершён для всех."""
        contest, params = self._contest()
        return contest.round_finished(state, params=params)

    def contest_leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        """Игрок вышел: его идущий раунд — ``void`` (очки прошлых раундов сохраняются)."""
        contest, _ = self._contest()
        return contest.leave(state, player_id)

    def contest_views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        """Статусы игроков для всех участников — без текстов ответов."""
        contest, _ = self._contest()
        return contest.views(state)

    def contest_outcome(self, state: ContestRoundState, player_id: str) -> RoundOutcome:
        """Итог раунда игрока (``RoundNotFinishedError``, пока его раунд идёт)."""
        contest, params = self._contest()
        return contest.outcome(state, player_id, params=params)

    def contest_score(self, state: ContestRoundState, player_id: str) -> int:
        """Очки игрока за завершённый раунд матча."""
        return self.score(self.contest_outcome(state, player_id))

    def contest_scores(self, state: ContestRoundState) -> dict[str, int]:
        """Очки всех игроков за раунд (раунд должен быть завершён).

        Raises:
            RoundNotFinishedError: раунд ещё идёт.
        """
        if not self.contest_round_finished(state):
            raise RoundNotFinishedError("раунд матча ещё идёт")
        return {pid: self.contest_score(state, pid) for pid in state.players}

    def contest_require_level_open(self, state: ContestRoundState, level: int) -> None:
        """Уровень можно выдать (D11): открыт сейчас или раньше; после раунда — все.

        Raises:
            LevelLockedError: уровень ещё не открыт.
        """
        opened = self.levels_count if state.finished else state.level
        if not 1 <= level <= opened:
            raise LevelLockedError(
                "уровень ещё не открыт", details={"level": level, "opened": opened}
            )
