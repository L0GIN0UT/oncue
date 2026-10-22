"""Правила состязания ``race`` и ``parallel`` (A2, ADR 0032): переходы, блокировки, время,
void вышедшего; property-тесты инвариантов на случайных последовательностях ходов."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gv_game_kernel import (
    ContestRoundState,
    Guess,
    GuessKind,
    LevelLockedError,
    MatchVerdict,
    PlayerStatus,
    RoundFinishedError,
    RoundNotFinishedError,
    RoundResult,
)
from gv_game_kernel.builtin import ParallelContest, ParallelParams, RaceContest, RaceParams

pytestmark = pytest.mark.unit

RACE = RaceContest()
PARALLEL = ParallelContest()
LEVELS = 3
WINDOW = 10_000
A, B, C = "a", "b", "c"


def race(attempts: int = 3, *, lock: bool = True) -> RaceParams:
    return RaceParams(attempts_per_level=attempts, lock_on_first_correct=lock)


def parallel(attempts: int = 1, *, all_answered: bool = True) -> ParallelParams:
    return ParallelParams(attempts_per_level=attempts, end_when_all_answered=all_answered)


def guess(state: ContestRoundState, verdict: str) -> Guess:
    if verdict == "skip":
        return Guess(kind=GuessKind.SKIP, level=state.level, verdict=None, display="")
    return Guess(kind=GuessKind.TEXT, level=state.level, verdict=MatchVerdict(verdict), display="x")


def play(
    rule: Any, state: ContestRoundState, player: str, verdict: str, now: int, *, params: Any
) -> ContestRoundState:
    result: ContestRoundState = rule.apply_guess(
        state, player, guess(state, verdict), levels_count=LEVELS, now_ms=now, params=params
    )
    return result


def advance(rule: Any, state: ContestRoundState, now: int, params: Any) -> ContestRoundState:
    result: ContestRoundState = rule.next_level(
        state, levels_count=LEVELS, now_ms=now, window_ms=WINDOW, params=params
    )
    return result


def statuses(rule: Any, state: ContestRoundState) -> dict[str, PlayerStatus]:
    return {v.player_id: v.status for v in rule.views(state)}


# ----------------------------------------------------------------------------- старт
def test_start_round_opens_level_one_for_everyone() -> None:
    state = RACE.start_round([A, B], now_ms=1000, window_ms=WINDOW, params=race())
    assert (state.level, state.level_opened_at_ms, state.level_ends_at_ms) == (1, 1000, 11000)
    assert list(state.players) == [A, B]
    assert all(p.state.level == 1 and p.active for p in state.players.values())
    assert statuses(RACE, state) == {A: PlayerStatus.THINKING, B: PlayerStatus.THINKING}


@pytest.mark.parametrize(
    ("players", "window"), [([], WINDOW), ([A, A], WINDOW), ([A, B], 0)], ids=str
)
def test_start_round_rejects_bad_call(players: list[str], window: int) -> None:
    with pytest.raises(ValueError):
        RACE.start_round(players, now_ms=0, window_ms=window, params=race())


def test_round_rules_allow_pass_but_not_give_up() -> None:
    rules = PARALLEL.round_rules(parallel(2))
    assert (rules.attempts_per_level, rules.skip_allowed, rules.give_up_allowed) == (2, True, False)


# ----------------------------------------------------------------------------- race
def test_race_first_exact_locks_level_for_opponent() -> None:
    p = race()
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(RACE, state, A, "exact", 2500, params=p)
    assert state.level_locked
    assert RACE.level_closed(state, now_ms=2500, params=p)
    with pytest.raises(LevelLockedError):
        play(RACE, state, B, "exact", 2500, params=p)  # та же миллисекунда — второй по порядку
    assert statuses(RACE, state)[A] is PlayerStatus.GUESSED
    # Уровень угадывания не виден, пока уровень не закрыт для всех (следующий не открыт).
    assert next(v for v in RACE.views(state) if v.player_id == A).level is None
    # Следующий уровень открывается сразу со своим окном: соперник ещё может угадать.
    state = advance(RACE, state, 2600, p)
    assert (state.level, state.level_locked, state.level_ends_at_ms) == (2, False, 12600)
    assert next(v for v in RACE.views(state) if v.player_id == A).level == 1
    state = play(RACE, state, B, "exact", 4000, params=p)
    assert RACE.level_closed(state, now_ms=4000, params=p)
    state = advance(RACE, state, 4000, p)
    assert RACE.round_finished(state, params=p)
    a, b = RACE.outcome(state, A, params=p), RACE.outcome(state, B, params=p)
    assert (a.result, a.level, a.elapsed_ms, a.window_ms) == (RoundResult.EXACT, 1, 2500, WINDOW)
    assert (b.result, b.level, b.elapsed_ms) == (RoundResult.EXACT, 2, 1400)


def test_race_without_lock_lets_both_guess_same_level() -> None:
    p = race(lock=False)
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(RACE, state, A, "exact", 100, params=p)
    assert not RACE.level_closed(state, now_ms=100, params=p)
    state = play(RACE, state, B, "exact", 200, params=p)
    assert RACE.level_closed(state, now_ms=200, params=p)  # больше никто не может ходить
    state = advance(RACE, state, 200, p)
    assert state.finished
    assert RACE.outcome(state, B, params=p).level == 1


def test_attempts_per_level_then_waits_for_next_level() -> None:
    p = race(attempts=2)
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(RACE, state, A, "miss", 1, params=p)
    assert statuses(RACE, state)[A] is PlayerStatus.THINKING
    state = play(RACE, state, A, "miss", 2, params=p)
    assert statuses(RACE, state)[A] is PlayerStatus.ANSWERED
    with pytest.raises(LevelLockedError) as info:
        play(RACE, state, A, "exact", 3, params=p)
    assert info.value.code == "game.level_locked"
    assert not RACE.level_closed(state, now_ms=3, params=p)  # B ещё может ходить
    # Окно истекло: ход отклоняется до next_level.
    with pytest.raises(LevelLockedError):
        play(RACE, state, B, "miss", WINDOW, params=p)
    state = advance(RACE, state, WINDOW, p)
    assert state.level == 2
    assert {pid: pr.state.level for pid, pr in state.players.items()} == {A: 2, B: 2}
    assert all(pr.state.attempts_on_level == 0 for pr in state.players.values())


def test_last_level_timeout_finishes_round_as_miss_or_partial() -> None:
    p = race(attempts=1)
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(RACE, state, A, "partial", 10, params=p)
    for level in range(1, LEVELS):
        state = advance(RACE, state, level * WINDOW, p)
    assert state.level == LEVELS
    state = advance(RACE, state, LEVELS * WINDOW, p)
    assert state.finished
    a, b = RACE.outcome(state, A, params=p), RACE.outcome(state, B, params=p)
    assert (a.result, a.partial_level, a.elapsed_ms) == (RoundResult.PARTIAL, 1, 10)
    assert b.result is RoundResult.MISS
    assert statuses(RACE, state) == {A: PlayerStatus.GUESSED, B: PlayerStatus.MISSED}


def test_skip_is_a_pass_on_the_level() -> None:
    p = parallel(attempts=2)
    state = PARALLEL.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(PARALLEL, state, A, "skip", 5, params=p)
    assert statuses(PARALLEL, state)[A] is PlayerStatus.ANSWERED
    with pytest.raises(LevelLockedError):
        play(PARALLEL, state, A, "miss", 6, params=p)


# ----------------------------------------------------------------------------- parallel
def test_parallel_level_closes_when_all_answered() -> None:
    p = parallel()
    state = PARALLEL.start_round([A, B, C], now_ms=0, window_ms=WINDOW, params=p)
    state = play(PARALLEL, state, A, "exact", 1000, params=p)
    assert not state.level_locked
    state = play(PARALLEL, state, B, "miss", 2000, params=p)
    assert not PARALLEL.level_closed(state, now_ms=2000, params=p)
    assert statuses(PARALLEL, state) == {
        A: PlayerStatus.GUESSED,
        B: PlayerStatus.ANSWERED,
        C: PlayerStatus.THINKING,
    }
    state = play(PARALLEL, state, C, "partial", 3000, params=p)
    assert PARALLEL.level_closed(state, now_ms=3000, params=p)
    state = advance(PARALLEL, state, 3000, p)
    assert state.level == 2
    state = play(PARALLEL, state, B, "exact", 3500, params=p)
    state = play(PARALLEL, state, C, "miss", 4000, params=p)
    state = advance(PARALLEL, state, 4000, p)
    assert state.level == 3
    state = play(PARALLEL, state, C, "miss", 4500, params=p)
    state = advance(PARALLEL, state, 4500, p)
    assert state.finished
    c = PARALLEL.outcome(state, C, params=p)
    assert (c.result, c.partial_level, c.elapsed_ms) == (RoundResult.PARTIAL, 1, 3000)
    assert PARALLEL.outcome(state, B, params=p).elapsed_ms == 500


def test_parallel_without_early_end_waits_for_window() -> None:
    p = parallel(all_answered=False)
    state = PARALLEL.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(PARALLEL, state, A, "miss", 1, params=p)
    state = play(PARALLEL, state, B, "miss", 2, params=p)
    assert not PARALLEL.level_closed(state, now_ms=WINDOW - 1, params=p)
    with pytest.raises(ValueError, match="открыт"):
        advance(PARALLEL, state, WINDOW - 1, p)
    assert PARALLEL.level_closed(state, now_ms=WINDOW, params=p)


def test_everyone_guessed_finishes_round_before_last_level() -> None:
    p = parallel(all_answered=False)
    state = PARALLEL.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(PARALLEL, state, A, "exact", 1, params=p)
    state = play(PARALLEL, state, B, "exact", 2, params=p)
    state = advance(PARALLEL, state, WINDOW, p)
    assert state.finished and state.level == 1


# ----------------------------------------------------------------------------- выход
def test_leaving_player_is_void_and_others_continue() -> None:
    p = parallel()
    state = PARALLEL.start_round([A, B, C], now_ms=0, window_ms=WINDOW, params=p)
    state = play(PARALLEL, state, A, "miss", 1, params=p)
    state = play(PARALLEL, state, B, "miss", 2, params=p)
    state = PARALLEL.leave(state, C)
    assert PARALLEL.level_closed(state, now_ms=3, params=p)  # оставшиеся ответили
    assert PARALLEL.outcome(state, C, params=p).result is RoundResult.VOID
    assert statuses(PARALLEL, state)[C] is PlayerStatus.MISSED
    with pytest.raises(RoundFinishedError):
        play(PARALLEL, state, C, "exact", 3, params=p)
    state = advance(PARALLEL, state, 3, p)
    assert state.level == 2 and state.players[C].state.level == 1


def test_leaving_after_guess_keeps_outcome() -> None:
    p = race()
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = play(RACE, state, A, "exact", 1, params=p)
    state = RACE.leave(state, A)
    assert RACE.outcome(state, A, params=p).result is RoundResult.EXACT
    assert not state.players[A].active


def test_all_players_left_closes_and_finishes() -> None:
    p = parallel(all_answered=False)
    state = PARALLEL.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    state = PARALLEL.leave(PARALLEL.leave(state, A), B)
    assert PARALLEL.level_closed(state, now_ms=1, params=p)
    assert advance(PARALLEL, state, 1, p).finished


# ----------------------------------------------------------------------------- ошибки
def test_errors_of_finished_round_and_bad_calls() -> None:
    p = race()
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    with pytest.raises(ValueError, match="не участвует"):
        play(RACE, state, "z", "miss", 1, params=p)
    with pytest.raises(ValueError, match="не участвует"):
        RACE.leave(state, "z")
    with pytest.raises(RoundNotFinishedError):
        RACE.outcome(state, A, params=p)
    wrong_level = Guess(kind=GuessKind.TEXT, level=2, verdict=MatchVerdict.MISS, display="")
    with pytest.raises(ValueError, match="уровне 2"):
        RACE.apply_guess(state, A, wrong_level, levels_count=LEVELS, now_ms=1, params=p)
    state = play(RACE, state, A, "exact", 1, params=p)
    with pytest.raises(RoundFinishedError):
        play(RACE, state, A, "miss", 2, params=p)
    state = advance(RACE, advance(RACE, state, 2, p), 2 + WINDOW, p)
    state = advance(RACE, state, 2 + 2 * WINDOW, p)
    assert state.finished
    with pytest.raises(RoundFinishedError):
        play(RACE, state, B, "miss", 3, params=p)
    with pytest.raises(RoundFinishedError):
        advance(RACE, state, 3, p)
    with pytest.raises(LevelLockedError):
        RACE.apply_guess(
            ContestRoundState(),
            A,
            guess(ContestRoundState(), "miss"),
            levels_count=1,
            now_ms=0,
            params=p,
        )


def test_next_level_rejects_non_positive_window() -> None:
    p = race(attempts=1)
    state = RACE.start_round([A, B], now_ms=0, window_ms=WINDOW, params=p)
    with pytest.raises(ValueError, match="окно"):
        RACE.next_level(state, levels_count=LEVELS, now_ms=WINDOW, window_ms=0, params=p)


# ----------------------------------------------------------------------------- property
moves = st.lists(
    st.tuples(
        st.sampled_from([A, B, C]),
        st.sampled_from(["exact", "partial", "miss", "skip", "tick", "leave"]),
        st.integers(min_value=0, max_value=4000),
    ),
    max_size=60,
)


def _run(rule: Any, params: Any, script: list[tuple[str, str, int]]) -> None:  # noqa: PLR0912 — один сценарий со всеми инвариантами
    """Прогнать сценарий, проверяя инварианты после каждого шага."""
    now = 0
    state = rule.start_round([A, B, C], now_ms=now, window_ms=WINDOW, params=params)
    attempts = params.attempts_per_level
    exact_on_level: dict[int, list[str]] = {}
    for player, action, dt in script:
        if state.finished:
            break
        now += dt  # время не убывает
        before = state
        try:
            if action == "tick":
                if rule.level_closed(state, now_ms=now, params=params):
                    state = advance(rule, state, now, params)
            elif action == "leave":
                state = rule.leave(state, player)
            else:
                state = play(rule, state, player, action, now, params=params)
                if action == "exact":
                    exact_on_level.setdefault(before.level, []).append(player)
        except (LevelLockedError, RoundFinishedError):
            assert state is before
            closed = rule.level_closed(before, now_ms=now, params=params)
            pr = before.players[player]
            assert closed or pr.state.finished or not pr.active or pr.state.level != before.level
            continue
        # Уровень общий не убывает, ходы игрока на уровне — не больше attempts_per_level.
        assert state.level >= before.level
        for pr in state.players.values():
            on_level = [g for g in pr.state.guesses if g.level == state.level]
            assert len(on_level) <= attempts
            assert pr.state.level >= min(state.level, LEVELS) or pr.state.finished
            if pr.elapsed_ms is not None:
                assert pr.elapsed_ms >= 0
        if params.__class__ is RaceParams and params.lock_on_first_correct:
            # race: после первого exact уровень закрыт — второго exact на уровне нет.
            assert all(len(v) <= 1 for v in exact_on_level.values())
    # Доиграть по таймеру: раунд обязательно заканчивается не более чем за LEVELS шагов.
    for _ in range(LEVELS + 1):
        if state.finished:
            break
        now += WINDOW
        state = advance(rule, state, now, params)
    assert state.finished
    for pid, pr in state.players.items():
        outcome = rule.outcome(state, pid, params=params)
        if not pr.active and outcome.result is RoundResult.VOID:
            assert outcome.level is None
        assert pr.state.finished


@settings(max_examples=150, deadline=None)
@given(
    script=moves,
    attempts=st.integers(min_value=1, max_value=3),
    lock=st.booleans(),
)
def test_race_invariants(script: list[tuple[str, str, int]], attempts: int, lock: bool) -> None:
    _run(RACE, race(attempts, lock=lock), script)


@settings(max_examples=150, deadline=None)
@given(
    script=moves,
    attempts=st.integers(min_value=1, max_value=3),
    all_answered=st.booleans(),
)
def test_parallel_invariants(
    script: list[tuple[str, str, int]], attempts: int, all_answered: bool
) -> None:
    _run(PARALLEL, parallel(attempts, all_answered=all_answered), script)


@settings(max_examples=100, deadline=None)
@given(script=moves)
def test_same_script_same_result(script: list[tuple[str, str, int]]) -> None:
    """Детерминизм: одинаковая последовательность ходов (и порядок в одну мс) — одинаковое
    состояние."""

    def final(rule: Any, params: Any) -> ContestRoundState:
        state: ContestRoundState = rule.start_round(
            [A, B, C], now_ms=0, window_ms=WINDOW, params=params
        )
        for player, action, _ in script:
            if action in {"tick", "leave"} or state.finished:
                continue
            try:
                state = play(rule, state, player, action, 0, params=params)  # все ходы в одну мс
            except (LevelLockedError, RoundFinishedError):
                continue
        return state

    assert final(RACE, race()) == final(RACE, race())
    assert final(PARALLEL, parallel(2)) == final(PARALLEL, parallel(2))
