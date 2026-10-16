"""Сложность сборника (D9): 1 − байесовское среднее доли очков первых завершений.

``score = 1 − (prior·m + Σ ratio) / (m + n)``; пока завершений ``n < min_plays`` —
сложности нет (``None``). Корзины: ``easy`` < ``t0`` ≤ ``medium`` < ``t1`` ≤ ``hard``.
Параметры — ``GV_COLLECTIONS__DIFFICULTY__*``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Difficulty(StrEnum):
    """Корзина сложности (фильтр каталога)."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class DifficultyModel:
    """Параметры формулы.

    Attributes:
        prior: Априорная доля очков (0..1).
        m: Вес априорной оценки.
        min_plays: Завершений, после которых сложность показывается.
        thresholds: Границы корзин ``(easy|medium, medium|hard)``.
    """

    prior: float
    m: int
    min_plays: int
    thresholds: tuple[float, float]

    def score(self, ratio_sum: float, plays: int) -> float | None:
        """Сложность 0..1 (больше — сложнее) или ``None``, пока завершений мало."""
        if plays < self.min_plays or plays + self.m <= 0:
            return None
        mean = (self.prior * self.m + ratio_sum) / (self.m + plays)
        return min(1.0, max(0.0, 1.0 - mean))

    def bucket(self, score: float | None) -> Difficulty | None:
        """Корзина по сложности."""
        if score is None:
            return None
        low, high = self.thresholds
        if score < low:
            return Difficulty.EASY
        if score < high:
            return Difficulty.MEDIUM
        return Difficulty.HARD

    def bounds(self, bucket: Difficulty) -> tuple[float, float]:
        """Полуинтервал ``[low, high)`` сложности корзины (фильтр каталога)."""
        low, high = self.thresholds
        return {
            Difficulty.EASY: (0.0, low),
            Difficulty.MEDIUM: (low, high),
            Difficulty.HARD: (high, 2.0),
        }[bucket]
