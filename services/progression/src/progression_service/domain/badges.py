"""Бейджи: выдаются один раз, когда метрика достигла цели (ADR 0042).

Значения метрик копятся в ``metric_counters`` (по игроку), бейдж не отзывается.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from progression_service.domain.config import BadgeDef, Band


def newly_unlocked(
    badges: Sequence[BadgeDef], values: Mapping[str, int], unlocked: Collection[str]
) -> list[BadgeDef]:
    """Бейджи, чья цель достигнута и которых у игрока ещё нет (включённые, в порядке конфига)."""
    return [
        b
        for b in badges
        if b.enabled and b.key not in unlocked and values.get(b.metric, 0) >= b.target
    ]


def season_tier(bands: Sequence[Band], place: int) -> int:
    """Звание на конец сезона по месту (упрощение этапа 5a).

    Звание - число полос сетки наград сезона ниже полосы игрока плюс один: лучшая полоса даёт
    наибольшее значение, последняя - 1. Игрок вне сетки звания не получает (0). Так цель бейджа
    ``season_tier`` в конфиге выбирает порог: 1 - любое место таблицы, выше - ближе к вершине.
    """
    for index, band in enumerate(bands):
        if band.first <= place <= band.last:
            return len(bands) - index
    return 0
