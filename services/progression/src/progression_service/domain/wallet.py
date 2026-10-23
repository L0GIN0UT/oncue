"""Начисление Версов: чистые функции от (результат, конфиг версии) (ADR 0043).

Суммы и сетки — в ``VersesConfig``. Потолок соло — ``experience.apply_cap`` с дневным счётчиком.
Правила антиабьюза общие с опытом (``OnlineResult``).
"""

from __future__ import annotations

from progression_service.domain.config import Band, VersesConfig, band_amount
from progression_service.domain.experience import MatchFormat, OnlineResult, Outcome


def solo_session_verses(cfg: VersesConfig, rounds_played: int) -> int:
    """Версы за соло-сессию до дневного потолка: ``solo_round`` за каждый сыгранный раунд."""
    return cfg.solo_round * max(0, rounds_played)


def daily_verses(cfg: VersesConfig) -> int:
    """Версы за дейлик."""
    return cfg.daily


def online_verses(cfg: VersesConfig, result: OnlineResult, min_rounds_ratio: float) -> int:
    """Версы за онлайн-матч.

    Дуэль: победа — ``duel.win``, иначе ``duel.loss`` (ничья и ``tech_win`` — как поражение:
    бонуса за победу нет). Party/lobby: по месту, только при выполненных бонусах за результат.
    Выход, ``forfeit`` и прерванный матч — 0.
    """
    if not result.rewarded:
        return 0
    bonuses = result.result_bonuses_allowed(min_rounds_ratio)
    if result.format is MatchFormat.DUEL:
        won = bonuses and result.outcome is Outcome.WIN
        return cfg.duel.win if won else cfg.duel.loss
    if not bonuses:
        return 0
    if 1 <= result.place <= len(cfg.party_by_place):
        return cfg.party_by_place[result.place - 1]
    return 0


def grid_amount(bands: tuple[Band, ...], place: int) -> int:
    """Награда по сетке (топ недели, итог сезона) за место; вне сетки — 0."""
    return band_amount(bands, place)
