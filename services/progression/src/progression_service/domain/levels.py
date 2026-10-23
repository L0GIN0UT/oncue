"""Уровень игрока по накопленному опыту (ADR 0042).

Переход к уровню ``n ≥ 2`` стоит ``base + step·n`` опыта (параметры — в конфиге), первый уровень
у нуля опыта. Накопленный опыт для уровня ``n`` — сумма стоимостей переходов ``2…n``.
Уровень не понижается: смена конфига не отнимает достигнутое.
"""

from __future__ import annotations

from dataclasses import dataclass

from progression_service.domain.config import LevelsConfig


@dataclass(frozen=True, slots=True)
class LevelProgress:
    """Положение внутри уровня.

    Attributes:
        level: Текущий уровень.
        xp_in_level: Набрано внутри уровня.
        xp_to_next: До следующего уровня; ``None`` на высшем.
    """

    level: int
    xp_in_level: int
    xp_to_next: int | None


def cost_to_reach(levels: LevelsConfig, level: int) -> int:
    """Опыт на переход с уровня ``level - 1`` на ``level`` (``level ≥ 2``)."""
    return levels.base + levels.step * level


def xp_for_level(levels: LevelsConfig, level: int) -> int:
    """Накопленный опыт, с которого начинается уровень ``level`` (у первого — 0)."""
    if level <= 1:
        return 0
    steps = level - 1
    # Σ_{k=2..level} (base + step·k) = steps·base + step·(level·(level+1)/2 − 1)
    return steps * levels.base + levels.step * (level * (level + 1) // 2 - 1)


def level_for_xp(levels: LevelsConfig, total_xp: int) -> int:
    """Наибольший уровень (не выше ``max_level``), чей порог не превышает ``total_xp``."""
    low, high = 1, levels.max_level
    while low < high:
        mid = (low + high + 1) // 2
        if xp_for_level(levels, mid) <= total_xp:
            low = mid
        else:
            high = mid - 1
    return low


def next_level(levels: LevelsConfig, current: int, total_xp: int) -> int:
    """Уровень после начисления: не ниже текущего."""
    return max(current, level_for_xp(levels, total_xp))


def progress(levels: LevelsConfig, total_xp: int, *, floor_level: int = 1) -> LevelProgress:
    """Уровень, прогресс внутри него и остаток до следующего.

    Args:
        levels: Кривая уровней.
        total_xp: Накопленный опыт.
        floor_level: Уже достигнутый уровень (после снижения порогов он не отнимается).
    """
    level = next_level(levels, floor_level, total_xp)
    in_level = max(0, total_xp - xp_for_level(levels, level))
    if level >= levels.max_level:
        return LevelProgress(level, in_level, None)
    return LevelProgress(level, in_level, max(1, xp_for_level(levels, level + 1) - total_xp))
