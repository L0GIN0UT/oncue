"""Фасад ``ModeEngine.contest_*`` для match (A5): сверка настоящим matcher-ом, окна уровней
из конфига, очки ``by_level``/``party_speed``, выдача уровней; соло-фасад не изменился."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gv_game_kernel import (
    ONLINE_SESSIONS,
    AnswerInput,
    AnswerKindError,
    EngineFactory,
    LevelLockedError,
    ModeEngine,
    ModeNotOnlineError,
    ModeNotSoloError,
    PlayerStatus,
    RoundDraw,
    RoundNotFinishedError,
    parse_mode_config,
)
from gv_testkit.game import builtin_registry, media_item

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
FACTORY = EngineFactory(builtin_registry(), supported_sessions=ONLINE_SESSIONS)
FILM = media_item("Брат", domain="film", aliases=("брат",))
DRAW = RoundDraw(target=FILM)


def _engine(seed_body: SeedBody, key: str) -> ModeEngine:
    return FACTORY.build(parse_mode_config(seed_body(key)))


def _text(text: str) -> AnswerInput:
    return AnswerInput(text=text)


def test_duel_race_through_facade(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, "film.duel.frame")
    assert engine.round_rules.attempts_per_level == 3
    assert engine.level_window_ms(1) == 15_000
    state = engine.contest_start(["a", "b"], now_ms=0)
    assert engine.contest_deadline_ms(state) == 15_000
    step = engine.contest_guess(state, "b", _text("сестра"), DRAW, display="сестра", now_ms=1)
    assert str(step.match.verdict) == "miss" and step.option is None
    step = engine.contest_guess(step.state, "a", _text("Брат"), DRAW, display="Брат", now_ms=2)
    assert str(step.match.verdict) == "exact"
    assert engine.contest_level_closed(step.state, now_ms=2)
    with pytest.raises(LevelLockedError):
        engine.contest_guess(step.state, "b", _text("Брат"), DRAW, display="Брат", now_ms=2)
    state = engine.contest_advance(step.state, now_ms=3)
    assert state.level == 2 and engine.contest_deadline_ms(state) == 15_003
    engine.contest_require_level_open(state, 2)
    with pytest.raises(LevelLockedError):
        engine.contest_require_level_open(state, 3)
    with pytest.raises(RoundNotFinishedError):
        engine.contest_scores(state)
    state = engine.contest_skip(state, "b", now_ms=4)  # пас: соперник больше не ходит
    assert engine.contest_level_closed(state, now_ms=4)
    state = engine.contest_advance(state, now_ms=5)
    assert state.level == 3 and not engine.contest_level_closed(state, now_ms=6)
    while not engine.contest_round_finished(state):  # дальше уровни закрываются по окну
        deadline = engine.contest_deadline_ms(state)
        assert deadline is not None
        state = engine.contest_advance(state, now_ms=deadline)
    assert engine.contest_deadline_ms(state) is None
    engine.contest_require_level_open(state, 6)  # после раунда открыты все уровни
    assert engine.contest_scores(state) == {"a": 6, "b": 0}  # by_level: [6, 5, …]
    assert [v.status for v in engine.contest_views(state)] == [
        PlayerStatus.GUESSED,
        PlayerStatus.MISSED,
    ]


def test_party_speed_through_facade(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, "film.party.frame")
    state = engine.contest_start(["a", "b", "c"], now_ms=1_000)
    state = engine.contest_guess(state, "a", _text("брат"), DRAW, display="", now_ms=6_000).state
    state = engine.contest_guess(state, "b", _text("нет"), DRAW, display="", now_ms=7_000).state
    state = engine.contest_leave(state, "c")
    assert engine.contest_level_closed(state, now_ms=7_000)
    state = engine.contest_advance(state, now_ms=7_000)
    state = engine.contest_guess(state, "b", _text("Брат"), DRAW, display="", now_ms=17_000).state
    state = engine.contest_advance(state, now_ms=17_000)
    assert engine.contest_round_finished(state)
    # a: t=5 с из T=20 с → 75; b: уровень 2, t=10 с → 50; c вышел — void, 0.
    assert engine.contest_scores(state) == {"a": 75, "b": 50, "c": 0}
    assert engine.contest_outcome(state, "c").result.value == "void"
    assert engine.max_score() == 100 * 10


def test_answer_kind_checked_before_move(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, "film.party.frame")
    state = engine.contest_start(["a"], now_ms=0)
    with pytest.raises(AnswerKindError):
        engine.contest_guess(state, "a", AnswerInput(option_id="x"), DRAW, display="", now_ms=0)


def test_level_window_out_of_range(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, "film.party.frame")
    with pytest.raises(ValueError, match="уровня 7"):
        engine.level_window_ms(7)


def test_solo_engine_has_no_contest_facade(seed_body: SeedBody) -> None:
    engine = EngineFactory(builtin_registry()).build(
        parse_mode_config(seed_body("film.random.frame"))
    )
    assert engine.contest is None
    assert engine.solo_session.kind == "solo"
    with pytest.raises(ModeNotOnlineError):
        engine.contest_start(["a"], now_ms=0)
    with pytest.raises(ModeNotOnlineError):
        engine.level_window_ms(1)


def test_online_engine_is_not_solo(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, "film.duel.frame")
    with pytest.raises(ModeNotSoloError):
        _ = engine.solo_session
    assert engine.online_session.kind == "duel"
