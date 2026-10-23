"""Серия дейлика по дням и домену (R2): ведёт progression сам, в событии серии нет."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class Streak:
    """Серия дейлика одного домена.

    Attributes:
        current: Подряд пройденных дней до ``last_day`` включительно.
        best: Лучшая серия за всё время.
        last_day: Последний засчитанный день; ``None`` — не проходил.
    """

    current: int = 0
    best: int = 0
    last_day: date | None = None

    def advance(self, day: date) -> tuple[Streak, bool]:
        """Засчитать прохождение дня.

        * следующий день после ``last_day`` — серия растёт;
        * пропуск — серия начинается заново с 1;
        * повтор уже засчитанного дня и опоздавшее событие за более ранний день — без изменений.

        Returns:
            Новая серия и признак «день засчитан впервые».
        """
        if self.last_day is not None:
            if day <= self.last_day:
                return self, False
            current = self.current + 1 if day == self.last_day + timedelta(days=1) else 1
        else:
            current = 1
        return Streak(current, max(self.best, current), day), True
