"""Эло дуэли: golden-таблица, симметрия, калибровка, множитель формата, штраф за выход."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rating_service.domain.elo import EloCalculator, Outcome, Side, round_half_away
from rating_service.domain.ladder import LadderConfig

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).resolve().parents[4] / "contracts" / "rating" / "examples"
OPPOSITE = {Outcome.WIN: Outcome.LOSS, Outcome.LOSS: Outcome.WIN, Outcome.DRAW: Outcome.DRAW}


def config(**patch: Any) -> LadderConfig:
    """Пример ladder-а с точечными правками (путь через ``__``)."""
    raw: dict[str, Any] = copy.deepcopy(
        json.loads((EXAMPLE / "ladder.film.v1.json").read_text(encoding="utf-8"))
    )
    for dotted, value in patch.items():
        target = raw
        *head, last = dotted.split("__")
        for part in head:
            target = target[part]
        target[last] = value
    return LadderConfig.parse(raw)


def side(rating: int, outcome: Outcome, *, matches: int = 100, left: bool = False) -> Side:
    return Side(rating=rating, matches_total=matches, outcome=outcome, left=left)


def settle(
    a: Side, b: Side, fmt: str = "classic", cfg: LadderConfig | None = None
) -> tuple[int, int]:
    cfg = cfg or config()
    first, second = EloCalculator(cfg.elo, cfg.calibration).settle(a, b, fmt)
    return first.total, second.total


@pytest.mark.parametrize(
    ("ra", "rb", "outcome", "expected"),
    [
        (1000, 1000, Outcome.WIN, (10, -10)),
        (1000, 1000, Outcome.DRAW, (0, 0)),
        (1000, 1200, Outcome.WIN, (15, -15)),
        (1000, 1200, Outcome.LOSS, (-5, 5)),
        (1400, 1000, Outcome.WIN, (2, -2)),
        (1400, 1000, Outcome.LOSS, (-18, 18)),
        (1000, 1400, Outcome.DRAW, (8, -8)),
    ],
)
def test_golden_table_with_regular_k(
    ra: int, rb: int, outcome: Outcome, expected: tuple[int, int]
) -> None:
    assert settle(side(ra, outcome), side(rb, OPPOSITE[outcome])) == expected


def test_calibrating_player_uses_k_calibrating() -> None:
    cfg = config()
    calc = EloCalculator(cfg.elo, cfg.calibration)
    fresh, veteran = calc.settle(
        side(1000, Outcome.WIN, matches=0), side(1000, Outcome.LOSS, matches=100), "classic"
    )
    assert fresh.total == round_half_away(cfg.elo.k_calibrating * 0.5)
    assert veteran.total == -round_half_away(cfg.elo.k * 0.5)


def test_calibration_ends_exactly_at_config_length() -> None:
    cfg = config(calibration__matches=3)
    calc = EloCalculator(cfg.elo, cfg.calibration)
    inside, _ = calc.settle(side(1000, Outcome.WIN, matches=2), side(1000, Outcome.LOSS), "classic")
    outside, _ = calc.settle(
        side(1000, Outcome.WIN, matches=3), side(1000, Outcome.LOSS), "classic"
    )
    assert inside.total > outside.total


def test_lite_multiplier_scales_delta() -> None:
    cfg = config(elo__k=100)
    classic = settle(side(1000, Outcome.WIN), side(1000, Outcome.LOSS), "classic", cfg)
    lite = settle(side(1000, Outcome.WIN), side(1000, Outcome.LOSS), "lite", cfg)
    assert classic == (50, -50)
    assert lite == (40, -40)


def test_leaver_gets_penalty_on_top_of_loss() -> None:
    cfg = config()
    calc = EloCalculator(cfg.elo, cfg.calibration)
    winner, leaver = calc.settle(
        side(1000, Outcome.WIN), side(1000, Outcome.LOSS, left=True), "classic"
    )
    assert winner.total == 10
    assert winner.leaver_penalty == 0
    assert leaver.leaver_penalty == cfg.elo.leaver_penalty
    assert leaver.total == -10 - cfg.elo.leaver_penalty


def test_zero_penalty_in_config_means_no_extra() -> None:
    cfg = config(elo__leaver_penalty=0)
    _, leaver = EloCalculator(cfg.elo, cfg.calibration).settle(
        side(1000, Outcome.WIN), side(1000, Outcome.LOSS, left=True), "classic"
    )
    assert leaver.total == -10
    assert leaver.leaver_penalty == 0


def test_unknown_format_is_rejected() -> None:
    cfg = config()
    with pytest.raises(KeyError):
        EloCalculator(cfg.elo, cfg.calibration).settle(
            side(1000, Outcome.WIN), side(1000, Outcome.LOSS), "party"
        )


def test_round_half_away_is_sign_symmetric() -> None:
    values = (7.5, -7.5, 0.49, -0.49, 0.5, 2.5)
    assert [round_half_away(x) for x in values] == [8, -8, 0, 0, 1, 3]


ratings = st.integers(min_value=-500, max_value=4000)
outcomes = st.sampled_from([Outcome.WIN, Outcome.LOSS, Outcome.DRAW])


@given(ra=ratings, rb=ratings, outcome=outcomes, k=st.integers(min_value=1, max_value=200))
def test_equal_k_and_no_penalty_is_zero_sum(ra: int, rb: int, outcome: Outcome, k: int) -> None:
    cfg = config(elo__k=k, elo__k_calibrating=k, elo__format_multiplier={"classic": 1, "lite": 1})
    a, b = settle(side(ra, outcome), side(rb, OPPOSITE[outcome]), "classic", cfg)
    assert a + b == 0


@given(gap=st.integers(min_value=0, max_value=2000))
def test_win_over_stronger_gives_at_least_win_over_weaker(gap: int) -> None:
    over_strong, _ = settle(side(1000, Outcome.WIN), side(1000 + gap, Outcome.LOSS))
    over_weak, _ = settle(side(1000, Outcome.WIN), side(1000 - gap, Outcome.LOSS))
    assert over_strong >= over_weak


@given(gap=st.integers(min_value=0, max_value=2000))
def test_loss_to_stronger_costs_at_most_loss_to_weaker(gap: int) -> None:
    to_strong, _ = settle(side(1000, Outcome.LOSS), side(1000 + gap, Outcome.WIN))
    to_weak, _ = settle(side(1000, Outcome.LOSS), side(1000 - gap, Outcome.WIN))
    assert to_strong >= to_weak


@given(ra=ratings, rb=ratings)
def test_winner_never_loses_and_loser_never_gains_without_penalty(ra: int, rb: int) -> None:
    a, b = settle(side(ra, Outcome.WIN), side(rb, Outcome.LOSS))
    assert a >= 0 >= b
