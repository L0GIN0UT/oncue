"""Переходы раунда (D9): уровни, попытки, пропуск, сдача, void, частичный ответ."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_game_kernel import (
    ActionNotAllowedError,
    Guess,
    GuessKind,
    LevelLockedError,
    MatchVerdict,
    RoundFinishedError,
    RoundNotFinishedError,
    RoundResult,
    RoundState,
    SessionSpec,
    apply_guess,
    apply_skip,
    close_round,
    give_up,
    require_level_open,
    round_outcome,
    void,
)

pytestmark = pytest.mark.unit


def _session(attempts: int = 1, *, skip: bool = True, give: bool = True) -> SessionSpec:
    return SessionSpec(
        kind="solo",
        rounds=5,
        attempts_per_level=attempts,
        skip_allowed=skip,
        give_up_allowed=give,
        allow_guest=True,
        abandon_after_min=60,
    )


def _guess(state: RoundState, verdict: MatchVerdict) -> Guess:
    return Guess(kind=GuessKind.TEXT, level=state.level, verdict=verdict, display="x")


def _answer(
    state: RoundState, verdict: MatchVerdict, *, levels: int = 6, attempts: int = 1
) -> RoundState:
    return apply_guess(
        state, _guess(state, verdict), levels_count=levels, session=_session(attempts)
    )


def test_exact_finishes_on_current_level() -> None:
    state = _answer(RoundState(), MatchVerdict.MISS)
    state = _answer(state, MatchVerdict.EXACT)
    assert state.outcome is RoundResult.EXACT
    assert state.level == 2
    assert [g.verdict for g in state.guesses] == [MatchVerdict.MISS, MatchVerdict.EXACT]
    assert round_outcome(state).level == 2


def test_miss_opens_next_level_and_last_miss_finishes() -> None:
    state = _answer(RoundState(), MatchVerdict.MISS, levels=3)
    assert state.level == 2
    state = _answer(state, MatchVerdict.MISS, levels=3)
    assert state.level == 3 and not state.finished
    state = _answer(state, MatchVerdict.MISS, levels=3)
    assert state.outcome is RoundResult.MISS
    assert state.level == 3


def test_attempts_per_level_keeps_level_until_exhausted() -> None:
    state = _answer(RoundState(), MatchVerdict.MISS, attempts=3)
    assert (state.level, state.attempts_on_level) == (1, 1)
    state = _answer(state, MatchVerdict.MISS, attempts=3)
    state = _answer(state, MatchVerdict.MISS, attempts=3)
    assert (state.level, state.attempts_on_level) == (2, 0)


def test_partial_is_awarded_once_and_round_continues() -> None:
    state = _answer(RoundState(), MatchVerdict.PARTIAL, levels=3)
    assert state.partial_awarded_level == 1 and not state.finished and state.level == 2
    state = _answer(state, MatchVerdict.PARTIAL, levels=3)
    assert state.partial_awarded_level == 1
    state = _answer(state, MatchVerdict.MISS, levels=3)
    assert state.outcome is RoundResult.PARTIAL
    outcome = round_outcome(state, elapsed_ms=1200)
    assert (outcome.level, outcome.partial_level, outcome.elapsed_ms) == (None, 1, 1200)


def test_partial_then_exact_keeps_both_levels() -> None:
    state = _answer(RoundState(), MatchVerdict.PARTIAL)
    state = _answer(state, MatchVerdict.EXACT)
    outcome = round_outcome(state)
    assert (outcome.result, outcome.level, outcome.partial_level) == (RoundResult.EXACT, 2, 1)


def test_skip_opens_next_level_and_finishes_on_last() -> None:
    state = apply_skip(RoundState(), levels_count=2, session=_session())
    assert state.level == 2 and state.guesses[-1].kind is GuessKind.SKIP
    assert state.guesses[-1].verdict is None
    state = apply_skip(state, levels_count=2, session=_session())
    assert state.outcome is RoundResult.MISS


def test_skip_resets_attempts_on_level() -> None:
    state = _answer(RoundState(), MatchVerdict.MISS, attempts=3)
    state = apply_skip(state, levels_count=6, session=_session(3))
    assert (state.level, state.attempts_on_level) == (2, 0)


def test_skip_and_give_up_respect_config() -> None:
    with pytest.raises(ActionNotAllowedError) as skip:
        apply_skip(RoundState(), levels_count=6, session=_session(skip=False))
    assert skip.value.code == "game.skip_not_allowed"
    with pytest.raises(ActionNotAllowedError) as gave:
        give_up(RoundState(), session=_session(give=False))
    assert gave.value.code == "game.give_up_not_allowed"


def test_give_up_keeps_partial() -> None:
    state = _answer(RoundState(), MatchVerdict.PARTIAL)
    state = give_up(state, session=_session())
    outcome = round_outcome(state)
    assert (outcome.result, outcome.partial_level) == (RoundResult.GAVE_UP, 1)


def test_void_has_no_levels() -> None:
    state = void(_answer(RoundState(), MatchVerdict.PARTIAL))
    outcome = round_outcome(state)
    assert (outcome.result, outcome.level, outcome.partial_level) == (RoundResult.VOID, None, None)


@pytest.mark.parametrize(
    "move",
    [
        lambda s: _answer(s, MatchVerdict.MISS),
        lambda s: apply_skip(s, levels_count=6, session=_session()),
        lambda s: give_up(s, session=_session()),
        void,
    ],
)
def test_finished_round_rejects_moves(move: object) -> None:
    finished = _answer(RoundState(), MatchVerdict.EXACT)
    with pytest.raises(RoundFinishedError) as info:
        move(finished)  # type: ignore[operator]  # параметризованный вызываемый объект
    assert info.value.code == "game.round_finished"


def test_guess_call_errors() -> None:
    skip = Guess(kind=GuessKind.SKIP, level=1, verdict=None, display="")
    with pytest.raises(ValueError, match="вердикт"):
        apply_guess(RoundState(), skip, levels_count=6, session=_session())
    wrong_level = Guess(kind=GuessKind.TEXT, level=2, verdict=MatchVerdict.MISS, display="")
    with pytest.raises(ValueError, match="уровне 2"):
        apply_guess(RoundState(), wrong_level, levels_count=6, session=_session())
    with pytest.raises(ValueError, match="уровень"):
        apply_skip(RoundState(), levels_count=0, session=_session())


def test_level_open_rules() -> None:
    state = _answer(RoundState(), MatchVerdict.MISS)
    require_level_open(state, 1, levels_count=6)
    require_level_open(state, 2, levels_count=6)
    with pytest.raises(LevelLockedError) as info:
        require_level_open(state, 3, levels_count=6)
    assert info.value.details == {"level": 3, "opened": 2}
    with pytest.raises(LevelLockedError):
        require_level_open(state, 0, levels_count=6)
    finished = _answer(state, MatchVerdict.EXACT)
    require_level_open(finished, 6, levels_count=6)


def test_outcome_requires_finished_round_and_valid_time() -> None:
    with pytest.raises(RoundNotFinishedError):
        round_outcome(RoundState())
    with pytest.raises(ValueError, match="elapsed_ms"):
        round_outcome(_answer(RoundState(), MatchVerdict.EXACT), elapsed_ms=-1)
    with pytest.raises(ValueError, match="window_ms"):
        round_outcome(_answer(RoundState(), MatchVerdict.EXACT), window_ms=0)
    outcome = round_outcome(_answer(RoundState(), MatchVerdict.EXACT), elapsed_ms=5, window_ms=9)
    assert (outcome.elapsed_ms, outcome.window_ms) == (5, 9)


def test_close_round_gives_partial_or_miss() -> None:
    """Матч: уровни кончились по таймеру — итог как после последнего промаха (ADR 0032)."""
    partial = apply_guess(
        RoundState(),
        Guess(GuessKind.TEXT, 1, MatchVerdict.PARTIAL, "x"),
        levels_count=3,
        session=_session(2),
    )
    assert close_round(partial).outcome is RoundResult.PARTIAL
    assert close_round(RoundState()).outcome is RoundResult.MISS
    with pytest.raises(RoundFinishedError):
        close_round(close_round(RoundState()))


# ----------------------------------------------------------------------------- свойства
moves = st.lists(st.sampled_from(["exact", "partial", "miss", "skip"]), min_size=1, max_size=60)


@given(
    levels=st.integers(min_value=1, max_value=8),
    attempts=st.integers(min_value=1, max_value=5),
    plan=moves,
)
def test_round_invariants(levels: int, attempts: int, plan: list[str]) -> None:
    """Уровень в 1..levels и не убывает, история растёт, раунд завершается не более чем
    за ``levels × attempts`` ходов, завершённый раунд не меняется."""
    session = _session(attempts)
    state = RoundState()
    for steps, move in enumerate(plan, start=1):
        if state.finished:
            break
        before = state
        if move == "skip":
            state = apply_skip(state, levels_count=levels, session=session)
        else:
            state = apply_guess(
                state,
                _guess(state, MatchVerdict(move)),
                levels_count=levels,
                session=session,
            )
        assert 1 <= state.level <= levels
        assert state.level >= before.level
        assert len(state.guesses) == len(before.guesses) + 1
        assert state.attempts_on_level <= attempts
        assert steps <= levels * attempts
    if state.finished:
        with pytest.raises(RoundFinishedError):
            apply_skip(state, levels_count=levels, session=session)


@given(levels=st.integers(min_value=1, max_value=8), attempts=st.integers(min_value=1, max_value=5))
def test_only_misses_finish_exactly_at_the_bound(levels: int, attempts: int) -> None:
    state = RoundState()
    steps = 0
    while not state.finished:
        state = _answer(state, MatchVerdict.MISS, levels=levels, attempts=attempts)
        steps += 1
    assert steps == levels * attempts
    assert state.outcome is RoundResult.MISS
