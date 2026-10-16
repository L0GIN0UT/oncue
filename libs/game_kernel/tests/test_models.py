"""Модели скелета: ошибки (HTTP-классы), ответ игрока, начальное состояние раунда."""

from __future__ import annotations

from uuid import uuid4

import pytest

from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError
from gv_game_kernel import (
    AnswerInput,
    ConfigError,
    LevelLockedError,
    RoundFinishedError,
    RoundResult,
    RoundState,
    RuleViolation,
)

pytestmark = pytest.mark.unit


def test_errors_map_to_expected_http_classes() -> None:
    assert isinstance(ConfigError(), InvalidInputError)
    assert ConfigError().code == "game.config_invalid"
    locked = LevelLockedError("уровень закрыт")
    assert isinstance(locked, ForbiddenError) and isinstance(locked, RuleViolation)
    assert locked.code == "game.level_locked"
    finished = RoundFinishedError("раунд завершён")
    assert isinstance(finished, ConflictError) and isinstance(finished, RuleViolation)
    assert finished.code == "game.round_finished"


def test_answer_is_exactly_one_of_item_text_option() -> None:
    assert AnswerInput(media_item_id=uuid4()).input_kind == "item"
    assert AnswerInput(text="Интерстеллар").input_kind == "text"
    assert AnswerInput(option_id="higher").input_kind == "option"
    with pytest.raises(ValueError, match="ровно одно"):
        AnswerInput()
    with pytest.raises(ValueError, match="ровно одно"):
        AnswerInput(media_item_id=uuid4(), text="x")
    with pytest.raises(ValueError, match="ровно одно"):
        AnswerInput(text="x", option_id="a")


def test_round_state_starts_on_first_level() -> None:
    state = RoundState()
    assert (state.level, state.attempts_on_level, state.guesses) == (1, 0, ())
    assert not state.finished
    assert RoundState(outcome=RoundResult.VOID).finished
