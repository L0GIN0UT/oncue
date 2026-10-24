"""Колесо: шансы по весам, выбор сектора через порт ``Random``, границы суток (ADR 0044).

Сектор выбирает только сервер: целочисленный бросок ``randrange(сумма весов)`` по накопленным
весам - без плавающей точки, шанс сектора точно ``вес / сумма``.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from gv_core.random import Random
from progression_service.domain.config import SectorDef


def chances(sectors: Sequence[SectorDef]) -> list[float]:
    """Шансы секторов: ``weight / сумма весов`` (публикуются игроку)."""
    total = sum(s.weight for s in sectors)
    return [s.weight / total for s in sectors]


def pick_sector(sectors: Sequence[SectorDef], rng: Random) -> SectorDef:
    """Выбрать сектор по весам."""
    roll = rng.randrange(sum(s.weight for s in sectors))
    for sector in sectors:
        if roll < sector.weight:
            return sector
        roll -= sector.weight
    raise AssertionError("бросок вне суммы весов")  # pragma: no cover - недостижимо


def wheel_day(at: datetime, tz: ZoneInfo) -> date:
    """Сутки колеса момента ``at`` в часовом поясе конфига."""
    return at.astimezone(tz).date()


STREAK_LOOKBACK_DAYS = 366
"""Сколько суток назад смотрим при расчёте серии (длиннее серия триггерам не нужна)."""


def spin_streak(spin_days: Collection[date], today: date) -> int:
    """Серия подряд идущих суток со спином, заканчивающаяся ``today`` (0 - сегодня спина нет)."""
    days = set(spin_days)
    streak = 0
    day = today
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def next_spin_at(day: date, tz: ZoneInfo) -> datetime:
    """Когда обновится колесо: начало следующих суток в зоне конфига (UTC)."""
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
