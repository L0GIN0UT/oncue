"""Неделя топа: понедельник в часовом поясе конфига ladder-а (ADR 0039)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from gv_core.domain import AggregateRoot
from rating_service.domain.events import WeeklyTopEntry, WeeklyTopFinalized


def week_start(at: datetime, timezone: str) -> date:
    """Понедельник недели, в которую попадает момент ``at`` (локальная дата в ``timezone``)."""
    if at.tzinfo is None:
        raise ValueError("нужен timezone-aware datetime")
    local = at.astimezone(ZoneInfo(timezone)).date()
    return date.fromordinal(local.toordinal() - local.weekday())


class WeeklyTop(AggregateRoot[str]):
    """Закрытый топ недели ladder-а; ``aggregate_id`` — ``{ladder}:{week_start}``."""

    def __init__(self, ladder: str, week: date) -> None:
        super().__init__(f"{ladder}:{week.isoformat()}")
        self.ladder = ladder
        self.week = week

    def finalize(self, season_id: UUID, ranked: Sequence[tuple[UUID, int]]) -> None:
        """Зафиксировать ``WeeklyTopFinalized``; места — по порядку ``ranked``."""
        self.record(
            WeeklyTopFinalized(
                aggregate_id=self.id,
                ladder=self.ladder,
                season_id=season_id,
                week_start=self.week.isoformat(),
                top=tuple(
                    WeeklyTopEntry(user_id=user_id, delta=delta, place=place)
                    for place, (user_id, delta) in enumerate(ranked, start=1)
                ),
            )
        )
