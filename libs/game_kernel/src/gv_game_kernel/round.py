"""Состояние раунда и переходы между уровнями (правила D9 плана этапа 2).

``RoundState`` неизменяем: каждый переход возвращает новое состояние. Переходы — чистые
функции от состояния, результата сверки и ``SessionSpec``; game хранит состояние в
сессии и сам решает, когда вызвать переход.

Скелет шага 0a: модели зафиксированы, реализацию переходов пишет поток A
(``docs/plans/stage-2.md`` §4, «round.py»).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from gv_game_kernel.spec import SessionSpec


class MatchVerdict(StrEnum):
    """Результат сверки одного ответа."""

    EXACT = "exact"
    PARTIAL = "partial"
    MISS = "miss"


class RoundResult(StrEnum):
    """Итог раунда (совпадает с ``rounds[].outcome`` в ``game.GameFinished``)."""

    EXACT = "exact"
    PARTIAL = "partial"
    MISS = "miss"
    GAVE_UP = "gave_up"
    VOID = "void"


class GuessKind(StrEnum):
    """Вид хода (``guesses[].kind`` в снимке сессии)."""

    ITEM = "item"
    TEXT = "text"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class Guess:
    """Ход игрока в раунде.

    Attributes:
        kind: Выбор из автокомплита, свободный текст или пропуск.
        level: Уровень, на котором сделан ход.
        verdict: Результат сверки (``None`` у пропуска).
        display: Что показать в истории попыток (название выбранной единицы или текст).
    """

    kind: GuessKind
    level: int
    verdict: MatchVerdict | None
    display: str


@dataclass(frozen=True, slots=True)
class RoundState:
    """Состояние раунда.

    Attributes:
        level: Текущий открытый уровень (с 1).
        attempts_on_level: Сколько ответов уже дано на текущем уровне.
        guesses: История ходов.
        outcome: Итог; ``None`` — раунд идёт.
        partial_awarded_level: Уровень, на котором засчитан частичный ответ (один раз).
    """

    level: int = 1
    attempts_on_level: int = 0
    guesses: tuple[Guess, ...] = ()
    outcome: RoundResult | None = None
    partial_awarded_level: int | None = None

    @property
    def finished(self) -> bool:
        """Раунд завершён (есть итог)."""
        return self.outcome is not None


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """Итог раунда для Scorer.

    Attributes:
        result: Итог раунда.
        level: Уровень точного ответа (``None``, если точного не было).
        partial_level: Уровень частичного ответа (``None``, если не было).
        elapsed_ms: Время от выдачи первого ассета раунда до ответа (для ``by_speed``).
    """

    result: RoundResult
    level: int | None = None
    partial_level: int | None = None
    elapsed_ms: int | None = None


def apply_guess(
    state: RoundState, guess: Guess, *, levels_count: int, session: SessionSpec
) -> RoundState:
    """Применить ответ: ``exact`` завершает раунд, промах после ``attempts_per_level``
    ответов открывает следующий уровень, после последнего уровня — ``miss``.

    Raises:
        RoundFinishedError: раунд уже завершён.
    """
    raise NotImplementedError("TODO(stage2/A): переходы RoundState")


def apply_skip(state: RoundState, *, levels_count: int, session: SessionSpec) -> RoundState:
    """Пропустить уровень (если ``session.skip_allowed``); на последнем уровне — ``miss``.

    Raises:
        RoundFinishedError: раунд уже завершён.
        RuleViolation: пропуск запрещён конфигом.
    """
    raise NotImplementedError("TODO(stage2/A): переходы RoundState")


def give_up(state: RoundState, *, session: SessionSpec) -> RoundState:
    """Сдаться: итог ``gave_up`` (если ``session.give_up_allowed``).

    Raises:
        RoundFinishedError: раунд уже завершён.
        RuleViolation: сдаваться запрещено конфигом.
    """
    raise NotImplementedError("TODO(stage2/A): переходы RoundState")


def void(state: RoundState) -> RoundState:
    """Аннулировать раунд (ассет недоступен): итог ``void``, раунд не входит в ``max_score``."""
    raise NotImplementedError("TODO(stage2/A): переходы RoundState")
