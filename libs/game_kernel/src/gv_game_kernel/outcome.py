"""Итог онлайн-матча: места, ничьи и исход дуэли (ADR 0032, D4 плана этапа 4).

Сигнатуры зафиксированы шагом 0a; реализация ``MatchOutcome.rank`` — поток A.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

from gv_core.errors import NotImplementedYetError


class DuelOutcome(StrEnum):
    """Исход для игрока дуэли (``players[].outcome`` в ``match.MatchFinished``)."""

    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


@dataclass(frozen=True, slots=True)
class Placement:
    """Место игрока в итоге матча.

    Attributes:
        player_id: Игрок.
        score: Очки за матч.
        place: Место с ничьими: ``1, 1, 3`` (одинаковые очки — одно место).
        outcome: Исход дуэли; ``None`` в матче на 3+ игроков.
        left: Игрок вышел до конца матча (ставится после оставшихся).
    """

    player_id: str
    score: int
    place: int
    outcome: DuelOutcome | None
    left: bool = False


class MatchOutcome:
    """Ранжирование игроков матча."""

    @staticmethod
    def rank(
        scores: Mapping[str, int],  # noqa: ARG004 — сигнатура 0a, реализация — поток A
        *,
        left: Collection[str] = (),  # noqa: ARG004
    ) -> list[Placement]:
        """Места по очкам: больше очков — выше; равные очки — одно место.

        Вышедшие (``left``) ранжируются после оставшихся, между собой — по очкам. Для двух
        игроков заполняется ``outcome`` (``win``/``loss``/``draw``).

        Args:
            scores: Очки каждого игрока.
            left: Игроки, вышедшие до конца матча.

        Returns:
            Места в порядке возрастания ``place``; при равенстве — по ``player_id``
            (детерминированно).
        """
        raise NotImplementedYetError("MatchOutcome.rank — поток A этапа 4a")


__all__ = ["DuelOutcome", "MatchOutcome", "Placement"]
