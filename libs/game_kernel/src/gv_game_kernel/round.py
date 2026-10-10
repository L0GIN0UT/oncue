"""Состояние раунда и переходы между уровнями (правила D9 плана этапа 2).

``RoundState`` неизменяем: каждый переход возвращает новое состояние. Переходы — чистые
функции от состояния, результата сверки и ``SessionSpec``; game хранит состояние в
сессии и сам решает, когда вызвать переход.

Правила (``docs/plans/stage-2.md`` §4, «round.py»; D9):
    * ``exact`` завершает раунд на текущем уровне;
    * промах или частичный ответ — ответ на уровне; после ``attempts_per_level`` ответов
      открывается следующий уровень, после последнего — итог раунда;
    * частичный ответ засчитывается один раз (первый уровень), раунд продолжается; если
      точного ответа так и не было, итог — ``partial``, иначе ``miss``;
    * пропуск открывает следующий уровень сразу (на последнем — итог), сдача — ``gave_up``,
      ``void`` — ассет недоступен (раунд не входит в ``max_score``).

Инварианты (проверяются property-тестами): уровень в ``1..levels_count``, уровень не
убывает, история только растёт, раунд завершается не более чем за
``levels_count × attempts_per_level`` ходов, завершённый раунд не меняется.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from gv_game_kernel.errors import (
    GIVE_UP_NOT_ALLOWED,
    SKIP_NOT_ALLOWED,
    ActionNotAllowedError,
    LevelLockedError,
    RoundFinishedError,
    RoundNotFinishedError,
)
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


def _require_active(state: RoundState) -> None:
    if state.finished:
        raise RoundFinishedError("раунд уже завершён", details={"outcome": str(state.outcome)})


def _require_levels(levels_count: int) -> None:
    if levels_count < 1:
        raise ValueError("у загадки должен быть хотя бы один уровень")


def _final_result(state: RoundState) -> RoundResult:
    return RoundResult.PARTIAL if state.partial_awarded_level is not None else RoundResult.MISS


def _next_level(state: RoundState, levels_count: int) -> RoundState:
    """Открыть следующий уровень или завершить раунд, если уровни кончились."""
    if state.level < levels_count:
        return replace(state, level=state.level + 1, attempts_on_level=0)
    return replace(state, outcome=_final_result(state))


def apply_guess(
    state: RoundState, guess: Guess, *, levels_count: int, session: SessionSpec
) -> RoundState:
    """Применить ответ: ``exact`` завершает раунд, промах после ``attempts_per_level``
    ответов открывает следующий уровень, после последнего уровня — ``miss`` (или
    ``partial``, если частичный ответ был засчитан).

    Args:
        state: Текущее состояние.
        guess: Ответ (``kind`` — ``item`` или ``text``, ``verdict`` задан, ``level`` —
            текущий уровень).
        levels_count: Число уровней загадки.
        session: Правила сессии режима.

    Raises:
        RoundFinishedError: раунд уже завершён.
        ValueError: ход-пропуск, ход без вердикта или с чужим уровнем (ошибка вызова).
    """
    _require_active(state)
    _require_levels(levels_count)
    if guess.kind is GuessKind.SKIP or guess.verdict is None:
        raise ValueError("ответ должен иметь вердикт; пропуск — apply_skip")
    if guess.level != state.level:
        raise ValueError(f"ответ на уровне {guess.level}, а открыт {state.level}")
    attempts = state.attempts_on_level + 1
    after = replace(state, attempts_on_level=attempts, guesses=(*state.guesses, guess))
    if guess.verdict is MatchVerdict.EXACT:
        return replace(after, outcome=RoundResult.EXACT)
    if guess.verdict is MatchVerdict.PARTIAL and after.partial_awarded_level is None:
        after = replace(after, partial_awarded_level=state.level)
    if attempts >= session.attempts_per_level:
        return _next_level(after, levels_count)
    return after


def apply_skip(state: RoundState, *, levels_count: int, session: SessionSpec) -> RoundState:
    """Пропустить уровень (если ``session.skip_allowed``); на последнем уровне — итог
    раунда (``miss`` или ``partial``).

    Raises:
        RoundFinishedError: раунд уже завершён.
        ActionNotAllowedError: пропуск запрещён конфигом (``game.skip_not_allowed``).
    """
    _require_active(state)
    _require_levels(levels_count)
    if not session.skip_allowed:
        raise ActionNotAllowedError("пропуск запрещён правилами режима", code=SKIP_NOT_ALLOWED)
    skip = Guess(kind=GuessKind.SKIP, level=state.level, verdict=None, display="")
    return _next_level(replace(state, guesses=(*state.guesses, skip)), levels_count)


def give_up(state: RoundState, *, session: SessionSpec) -> RoundState:
    """Сдаться: итог ``gave_up`` (если ``session.give_up_allowed``).

    Засчитанный ранее частичный ответ сохраняется (``partial_awarded_level``): очки за
    него уже были показаны игроку, а Scorer начисляет их по ``RoundOutcome.partial_level``.

    Raises:
        RoundFinishedError: раунд уже завершён.
        ActionNotAllowedError: сдаваться запрещено конфигом (``game.give_up_not_allowed``).
    """
    _require_active(state)
    if not session.give_up_allowed:
        raise ActionNotAllowedError(
            "сдаваться запрещено правилами режима", code=GIVE_UP_NOT_ALLOWED
        )
    return replace(state, outcome=RoundResult.GAVE_UP)


def void(state: RoundState) -> RoundState:
    """Аннулировать раунд (ассет недоступен): итог ``void``, раунд не входит в ``max_score``.

    Raises:
        RoundFinishedError: раунд уже завершён (итог не переписывается).
    """
    _require_active(state)
    return replace(state, outcome=RoundResult.VOID)


def require_level_open(state: RoundState, level: int, *, levels_count: int) -> None:
    """Проверить, что уровень можно выдать клиенту (D11).

    В идущем раунде открыты уровни ``1..state.level``; после завершения — все уровни
    (ответ уже не угадать, а ревил всё равно отдаёт последний уровень).

    Raises:
        LevelLockedError: уровень вне открытого диапазона (403 ``game.level_locked``).
    """
    _require_levels(levels_count)
    opened = levels_count if state.finished else state.level
    if not 1 <= level <= opened:
        raise LevelLockedError("уровень ещё не открыт", details={"level": level, "opened": opened})


def round_outcome(state: RoundState, *, elapsed_ms: int | None = None) -> RoundOutcome:
    """Итог завершённого раунда для Scorer и события ``GameFinished``.

    ``level`` — уровень точного ответа (иначе ``None``), ``partial_level`` — уровень, на
    котором засчитан частичный ответ. У ``void`` оба поля пустые: раунд не оценивается.

    Args:
        state: Завершённое состояние.
        elapsed_ms: Время от выдачи первого ассета раунда до последнего хода (считает game).

    Raises:
        RoundNotFinishedError: раунд ещё идёт.
        ValueError: отрицательное время.
    """
    if state.outcome is None:
        raise RoundNotFinishedError("раунд ещё не завершён")
    if elapsed_ms is not None and elapsed_ms < 0:
        raise ValueError("elapsed_ms не может быть отрицательным")
    if state.outcome is RoundResult.VOID:
        return RoundOutcome(result=RoundResult.VOID, elapsed_ms=elapsed_ms)
    return RoundOutcome(
        result=state.outcome,
        level=state.level if state.outcome is RoundResult.EXACT else None,
        partial_level=state.partial_awarded_level,
        elapsed_ms=elapsed_ms,
    )
