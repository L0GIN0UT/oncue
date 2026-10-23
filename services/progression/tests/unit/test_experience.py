"""Домен: чистые функции начисления опыта и Версов (D3, D5, R4, R5)."""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import experience, wallet
from progression_service.domain.config import ProgressionConfig
from progression_service.domain.experience import Finish, MatchFormat, OnlineResult, Outcome

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg(make_config: Callable[..., dict[str, Any]]) -> ProgressionConfig:
    return ProgressionConfig.parse(make_config())


def result(**kw: Any) -> OnlineResult:
    base: dict[str, Any] = {
        "format": MatchFormat.DUEL,
        "finish": Finish.COMPLETED,
        "outcome": Outcome.WIN,
        "place": 1,
        "rounds_total": 4,
        "rounds_played": 4,
        "rounds_won": 3,
    }
    return OnlineResult(**{**base, **kw})


# ------------------------------------------------------------------ соло и дейлик
def test_solo_round_is_base_plus_points_capped(cfg: ProgressionConfig) -> None:
    rnd = cfg.xp.solo_round
    assert experience.solo_round_xp(rnd, 0) == 20
    assert experience.solo_round_xp(rnd, 150) == 35
    assert experience.solo_round_xp(rnd, 10_000) == 40
    assert experience.solo_session_xp(cfg.xp, [0, 150, 10_000]) == 20 + 35 + 40


def test_daily_xp_grows_with_streak_up_to_max(cfg: ProgressionConfig) -> None:
    assert [experience.daily_xp(cfg.xp.daily, s) for s in (1, 2, 5, 50)] == [60, 70, 100, 100]


# ------------------------------------------------------------------ онлайн
def test_duel_win_gets_participation_rounds_and_bonus(cfg: ProgressionConfig) -> None:
    out = experience.online_xp(cfg.xp, result(), cfg.min_rounds_ratio)
    assert out.match_xp == 40 + 10 * 3 + 60
    assert out.first_win_eligible
    assert wallet.online_verses(cfg.verses, result(), cfg.min_rounds_ratio) == 10


def test_duel_loss_and_draw_get_no_win_bonus_and_no_first_win(cfg: ProgressionConfig) -> None:
    loss = result(outcome=Outcome.LOSS, place=2, rounds_won=1)
    assert experience.online_xp(cfg.xp, loss, 0.5) == experience.OnlineXp(
        50, first_win_eligible=False
    )
    assert wallet.online_verses(cfg.verses, loss, 0.5) == 3
    draw = result(outcome=Outcome.DRAW, rounds_won=2)
    out = experience.online_xp(cfg.xp, draw, 0.5)
    assert out.match_xp == 60
    assert not out.first_win_eligible


@pytest.mark.parametrize("finish", [Finish.FORFEIT, Finish.LEFT, Finish.TECH_LOSS])
def test_leavers_get_nothing(cfg: ProgressionConfig, finish: Finish) -> None:
    r = result(finish=finish, outcome=Outcome.LOSS)
    assert experience.online_xp(cfg.xp, r, 0.5) == experience.OnlineXp(0, first_win_eligible=False)
    assert wallet.online_verses(cfg.verses, r, 0.5) == 0


def test_aborted_match_gives_nothing(cfg: ProgressionConfig) -> None:
    r = result(aborted=True)
    assert experience.online_xp(cfg.xp, r, 0.5).match_xp == 0
    assert wallet.online_verses(cfg.verses, r, 0.5) == 0


def test_tech_win_gets_participation_and_rounds_without_win_bonus(cfg: ProgressionConfig) -> None:
    r = result(finish=Finish.TECH_WIN, rounds_won=2)
    out = experience.online_xp(cfg.xp, r, 0.5)
    assert out == experience.OnlineXp(40 + 20, first_win_eligible=False)
    assert wallet.online_verses(cfg.verses, r, 0.5) == 3


def test_win_bonus_needs_enough_rounds_played(cfg: ProgressionConfig) -> None:
    short = result(rounds_played=1, rounds_won=1)  # 1 из 4 < 0.5
    out = experience.online_xp(cfg.xp, short, cfg.min_rounds_ratio)
    assert out == experience.OnlineXp(40 + 10, first_win_eligible=False)
    assert wallet.online_verses(cfg.verses, short, cfg.min_rounds_ratio) == 3
    exactly_half = result(rounds_played=2, rounds_won=2)
    assert experience.online_xp(cfg.xp, exactly_half, 0.5).first_win_eligible


def test_duel_without_rounds_gets_participation_only(cfg: ProgressionConfig) -> None:
    """Матч без раундов не выполнил ``min_rounds_ratio``: ни бонуса победы, ни первой победы."""
    empty = result(rounds_total=0, rounds_played=0, rounds_won=0)
    out = experience.online_xp(cfg.xp, empty, cfg.min_rounds_ratio)
    assert out == experience.OnlineXp(40, first_win_eligible=False)
    assert not empty.result_bonuses_allowed(cfg.min_rounds_ratio)
    assert wallet.online_verses(cfg.verses, empty, cfg.min_rounds_ratio) == 3  # как за поражение


@pytest.mark.parametrize("place", [1, 2, 3])
def test_party_without_rounds_gets_no_place_bonus_and_no_verses(
    cfg: ProgressionConfig, place: int
) -> None:
    empty = result(
        format=MatchFormat.LOBBY, outcome=None, place=place, rounds_total=0, rounds_played=0
    )
    out = experience.online_xp(cfg.xp, empty, cfg.min_rounds_ratio)
    assert out == experience.OnlineXp(40, first_win_eligible=False)
    assert wallet.online_verses(cfg.verses, empty, cfg.min_rounds_ratio) == 0


@pytest.mark.parametrize(
    ("place", "bonus", "verses"),
    [(1, 80, 10), (2, 50, 6), (3, 30, 4), (4, 10, 2), (6, 10, 0)],
)
def test_party_by_place(cfg: ProgressionConfig, place: int, bonus: int, verses: int) -> None:
    r = result(format=MatchFormat.LOBBY, outcome=None, place=place)
    assert experience.online_xp(cfg.xp, r, 0.5).match_xp == 40 + bonus
    assert wallet.online_verses(cfg.verses, r, 0.5) == verses


def test_first_win_in_party_is_place_one_only(cfg: ProgressionConfig) -> None:
    first = result(format=MatchFormat.PARTY, outcome=None, place=1)
    second = result(format=MatchFormat.PARTY, outcome=None, place=2)
    assert experience.online_xp(cfg.xp, first, 0.5).first_win_eligible
    assert not experience.online_xp(cfg.xp, second, 0.5).first_win_eligible


# ------------------------------------------------------------------ сетки
def test_weekly_and_season_grids(cfg: ProgressionConfig) -> None:
    assert wallet.grid_amount(cfg.verses.weekly_top, 1) == 500
    assert wallet.grid_amount(cfg.verses.weekly_top, 100) == 30
    assert wallet.grid_amount(cfg.verses.weekly_top, 101) == 0
    assert wallet.grid_amount(cfg.verses.season, 1000) == 50


# ------------------------------------------------------------------ потолок
@given(
    raw=st.lists(st.integers(0, 500), max_size=30),
    cap=st.integers(0, 2000),
    seed=st.integers(0, 10_000),
)
def test_daily_cap_holds_for_any_order_and_split(raw: list[int], cap: int, seed: int) -> None:
    def total(amounts: list[int]) -> int:
        used = 0
        for amount in amounts:
            used += experience.apply_cap(amount, used, cap)
            assert used <= cap
        return used

    shuffled = list(raw)
    random.Random(seed).shuffle(shuffled)
    assert total(raw) == total(shuffled) == min(cap, sum(raw))
    # Разбиение события на части даёт ту же сумму: потолок не зависит от нарезки.
    pieces = list(itertools.chain.from_iterable([a // 2, a - a // 2] for a in raw))
    assert total(pieces) == min(cap, sum(raw))


@given(amount=st.integers(0, 10_000), used=st.integers(0, 10_000), cap=st.integers(0, 10_000))
def test_apply_cap_is_within_bounds(amount: int, used: int, cap: int) -> None:
    granted = experience.apply_cap(amount, used, cap)
    assert 0 <= granted <= amount
    assert used + granted <= max(used, cap)


@given(points=st.integers(0, 10**6), bigger=st.integers(0, 10**6))
def test_solo_round_xp_is_monotonic_in_points(points: int, bigger: int) -> None:
    from progression_service.domain.config import SoloRoundXp

    rnd = SoloRoundXp(base=20, score_divisor=10, max=40)
    assert experience.solo_round_xp(rnd, points) <= experience.solo_round_xp(rnd, points + bigger)
