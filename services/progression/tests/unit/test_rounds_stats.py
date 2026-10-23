"""Раунды игрока в матче: сколько сыграно и выиграно (вход антиабьюза R5)."""

from __future__ import annotations

from uuid import UUID

import pytest
from progression_service.application.accrual.match import rounds_stats

pytestmark = pytest.mark.unit

A, B, C = UUID(int=1), UUID(int=2), UUID(int=3)


def test_played_counts_rounds_with_player_and_won_needs_strictly_best_score() -> None:
    rounds = (
        ((A, 10), (B, 0)),  # A выиграл
        ((A, 5), (B, 5)),  # поровну — никто
        ((B, 10),),  # A не участвовал
        ((A, 0), (B, 0)),  # ноль очков — не выигрыш
    )
    assert rounds_stats(rounds, A) == (3, 1)
    assert rounds_stats(rounds, B) == (4, 1)


def test_multiplayer_round_is_won_only_over_all_others() -> None:
    rounds = (((A, 7), (B, 5), (C, 9)),)
    assert rounds_stats(rounds, C) == (1, 1)
    assert rounds_stats(rounds, A) == (1, 0)


def test_no_rounds() -> None:
    assert rounds_stats((), A) == (0, 0)
