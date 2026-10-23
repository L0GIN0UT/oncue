"""Эло дуэли (ADR 0037): ожидание, дельта, калибровка, множитель формата, штраф за выход.

``E = 1 / (1 + 10^((R_соперника − R) / scale))``; ``delta = round(K × mult × (S − E))``.
Округление — от нуля (``round_half_away``): оно симметрично по знаку, поэтому при равных K и
без штрафа дельты пары в сумме дают ровно ноль. Ожидание считается один раз для пары от
лица более сильного игрока, а для второго берётся ``1 − E`` — так симметрия точная и во
float, а не «с точностью до эпсилон».
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from rating_service.domain.ladder import CalibrationConfig, EloConfig

_MAX_EXPONENT = 300.0


class Outcome(StrEnum):
    """Исход матча для игрока."""

    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"

    @property
    def score(self) -> float:
        """Очки ``S`` формулы Эло: 1 / 0 / 0,5."""
        return {Outcome.WIN: 1.0, Outcome.LOSS: 0.0, Outcome.DRAW: 0.5}[self]


@dataclass(frozen=True, slots=True)
class Side:
    """Игрок на входе расчёта.

    Attributes:
        rating: Рейтинг до матча.
        matches_total: Рейтинговых матчей до этого (определяет калибровку).
        outcome: Исход.
        left: Вышел или сдался (``leaver_penalty``).
    """

    rating: int
    matches_total: int
    outcome: Outcome
    left: bool = False


@dataclass(frozen=True, slots=True)
class EloDelta:
    """Результат для игрока: ``total = base − leaver_penalty``."""

    base: int
    leaver_penalty: int

    @property
    def total(self) -> int:
        """Полная дельта рейтинга."""
        return self.base - self.leaver_penalty


def round_half_away(value: float) -> int:
    """Округление «от нуля»: 7,5 → 8, −7,5 → −8."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


class EloCalculator:
    """Расчёт дельт пары по конфигу ladder-а."""

    def __init__(self, elo: EloConfig, calibration: CalibrationConfig) -> None:
        self._elo = elo
        self._calibration = calibration

    def expected(self, rating: int, opponent: int) -> float:
        """Ожидаемый результат ``E`` игрока против соперника."""
        # Показатель ограничен: `10 ** x` переполняет float за ~308, а рейтинги могут разойтись.
        exponent = max(-_MAX_EXPONENT, min(_MAX_EXPONENT, (opponent - rating) / self._elo.scale))
        return float(1.0 / (1.0 + 10.0**exponent))

    def k_for(self, matches_total: int) -> float:
        """``K`` игрока: повышенный в калибровке."""
        calibrating = matches_total < self._calibration.matches
        return self._elo.k_calibrating if calibrating else self._elo.k

    def settle(self, a: Side, b: Side, queue_format: str) -> tuple[EloDelta, EloDelta]:
        """Дельты обоих игроков.

        Args:
            a: Первый игрок.
            b: Второй игрок.
            queue_format: Формат очереди — выбирает множитель из конфига.

        Raises:
            KeyError: у формата нет множителя в конфиге.
        """
        multiplier = self._elo.format_multiplier[queue_format]
        if a.rating >= b.rating:
            expected_a = self.expected(a.rating, b.rating)
            expected_b = 1.0 - expected_a
        else:
            expected_b = self.expected(b.rating, a.rating)
            expected_a = 1.0 - expected_b
        return (
            self._delta(a, expected_a, multiplier),
            self._delta(b, expected_b, multiplier),
        )

    def _delta(self, side: Side, expected: float, multiplier: float) -> EloDelta:
        k = self.k_for(side.matches_total)
        base = round_half_away(k * multiplier * (side.outcome.score - expected))
        return EloDelta(base=base, leaver_penalty=self._elo.leaver_penalty if side.left else 0)
