"""Движок одного режима — фасад ядра для game (соло) и match (этап 4).

``ModeEngine`` собирает ``EngineFactory.build`` из валидного ``ModeConfig``: параметры
плагинов уже разобраны их ``params_schema``, а сервис работает с готовыми операциями —
уровни, розыгрыш, сверка ответа, переходы раунда, выдача ассета уровня, очки. Сам движок
без состояния и IO (порты передаются в вызовы), его можно кэшировать по
``(mode_key, version)``.

Недоступный ассет (``GoneError`` из ``AssetPort``) движок не перехватывает: game ловит
ошибку и вызывает ``void`` — раунд аннулируется и не входит в ``max_score``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from uuid import UUID

from pydantic import BaseModel

from gv_game_kernel.errors import RoundFinishedError, RoundNotFinishedError
from gv_game_kernel.params import params_hash
from gv_game_kernel.plugins import (
    AnswerFormat,
    AnswerInput,
    AnswerMatcher,
    ClueType,
    ClueView,
    DrawContext,
    EndRule,
    LevelSpec,
    MatchContext,
    MatchResult,
    PartialRule,
    Preset,
    Scorer,
    Source,
)
from gv_game_kernel.ports import AssetPort, CandidatePort, CollectionPort, PlayableFor, PoolQuery
from gv_game_kernel.refs import AssetRef, MediaItemRef
from gv_game_kernel.round import (
    Guess,
    GuessKind,
    RoundOutcome,
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
    """

    state: RoundState
    match: MatchResult


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
        self._match_ctx = MatchContext(
            domain=config.domain,
            normalizer=normalizer or answer_normalizer(),
            partial_rules=partials,
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
        self, state: RoundState, asset: AssetRef, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Ассет ревила — только у завершённого раунда (D11).

        Raises:
            RoundNotFinishedError: раунд идёт.
        """
        if not state.finished:
            raise RoundNotFinishedError("ревил доступен только после раунда")
        return await self.clue.final_view(asset, self.clue_params, assets=assets, subject=subject)

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
        )

    async def draw(self, ctx: DrawContext, n: int | None = None) -> list[MediaItemRef]:
        """Разыграть ``n`` единиц (по умолчанию ``session.rounds``) через Source режима.

        Raises:
            ValueError: контекст собран для другого режима или загадки.
            PoolTooSmallError | PresetNotFoundError: ошибки источника.
        """
        if (ctx.mode_key, ctx.clue_type, ctx.params_hash) != (
            self.config.key,
            self.clue.key,
            self.clue_params_hash,
        ):
            raise ValueError("DrawContext собран для другого режима — см. draw_context()")
        return await self.source.draw(ctx, self.source_params, n or self.config.session.rounds)

    # ------------------------------------------------------------------------- раунд
    def check(self, answer: AnswerInput, item: MediaItemRef) -> MatchResult:
        """Сверить ответ с загаданной единицей (matcher + правила частичного ответа)."""
        return self.matcher.match(answer, item, self.matcher_params, self._match_ctx)

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
        self, state: RoundState, answer: AnswerInput, item: MediaItemRef, *, display: str
    ) -> AnswerStep:
        """Сверить ответ и применить его к раунду.

        Args:
            state: Состояние раунда (идущего).
            answer: Ответ игрока.
            item: Загаданная единица.
            display: Что показать в истории попыток (название выбранной единицы или текст).

        Raises:
            RoundFinishedError: раунд уже завершён.
        """
        if state.finished:
            # В завершённом раунде ответ не сверяем: matcher не должен подсказывать ответ.
            raise RoundFinishedError("раунд уже завершён", details={"outcome": str(state.outcome)})
        match = self.check(answer, item)
        kind = GuessKind.ITEM if answer.media_item_id is not None else GuessKind.TEXT
        guess = Guess(kind=kind, level=state.level, verdict=match.verdict, display=display)
        return AnswerStep(state=self.advance(state, guess), match=match)

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
