"""Итог онлайн-матча: места, ничьи и исход дуэли (ADR 0032, D4 плана этапа 4).

Сигнатуры зафиксированы шагом 0a. Вышедший игрок ставится после всех оставшихся, даже
если очков у него больше: в дуэли вышедший проигрывает (тех. победа соперника).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

DUEL_PLAYERS = 2
"""Игроков в дуэли: только у такого матча заполняется ``Placement.outcome``."""


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
        scores: Mapping[str, int],
        *,
        left: Collection[str] = (),
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

        Raises:
            ValueError: в ``left`` есть игрок, которого нет в ``scores``.
        """
        gone = frozenset(left)
        unknown = gone - scores.keys()
        if unknown:
            raise ValueError(f"вышедших нет среди игроков: {sorted(unknown)}")
        stayed = sorted((p for p in scores if p not in gone), key=lambda p: (-scores[p], p))
        quit_ = sorted((p for p in scores if p in gone), key=lambda p: (-scores[p], p))
        places: list[tuple[str, int, bool]] = []
        for group, is_left in ((stayed, False), (quit_, True)):
            offset = len(places)
            for i, player in enumerate(group):
                tied = i > 0 and scores[group[i - 1]] == scores[player]
                place = places[-1][1] if tied else offset + i + 1
                places.append((player, place, is_left))
        duel = len(places) == DUEL_PLAYERS
        return [
            Placement(
                player_id=player,
                score=scores[player],
                place=place,
                outcome=_duel_outcome(place, places) if duel else None,
                left=is_left,
            )
            for player, place, is_left in places
        ]


def _duel_outcome(place: int, places: list[tuple[str, int, bool]]) -> DuelOutcome:
    """Исход дуэли по местам: одинаковые места — ничья, первое — победа."""
    if places[0][1] == places[1][1]:
        return DuelOutcome.DRAW
    return DuelOutcome.WIN if place == 1 else DuelOutcome.LOSS


__all__ = ["DuelOutcome", "MatchOutcome", "Placement"]
