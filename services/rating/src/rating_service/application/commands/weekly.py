"""Топ недели: закрытие прошедших недель (ADR 0039)."""

from __future__ import annotations

import structlog

from gv_core.clock import Clock
from rating_service.application.ports import UnitOfWorkFactory
from rating_service.domain.weekly import WeeklyTop, week_start

log = structlog.get_logger(__name__)


class FinalizeWeeklyTops:
    """Закрывает недели до текущей: ``WeeklyTopFinalized`` один раз на ``(ladder, week_start)``.

    Идемпотентно по периоду (`weekly_tops` с первичным ключом), под advisory lock задачи.
    Неделя без положительных приростов закрывается без события (`season_id` в таблице null).
    «Сезон» события — сезон последнего прироста недели: он один для всего топа, даже если
    неделя пересекла границу сезонов (у каждого игрока при этом свой рейтинг в своём сезоне).
    """

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self) -> int:
        """Закрыть недели всех ladder-ов; число событий."""
        async with self._uow() as uow:
            published = await uow.ladders.all_published()
        events = 0
        for item in published:
            config = item.config
            now = self._clock.now()
            current = week_start(now, config.weekly.timezone)
            async with self._uow() as uow:
                for week in await uow.weekly.pending_weeks(config.key, current):
                    top = await uow.weekly.top(
                        config.key,
                        week,
                        limit=config.weekly.top_size,
                        calibration_matches=config.calibration.matches,
                    )
                    season_id = await uow.weekly.week_season(config.key, week) if top else None
                    payload = [
                        {"user_id": str(r.user_id), "delta": r.delta, "place": place}
                        for place, r in enumerate(top, start=1)
                    ]
                    closed = await uow.weekly.finalize(config.key, week, season_id, payload, now)
                    if closed and top and season_id is not None:
                        weekly = WeeklyTop(config.key, week)
                        weekly.finalize(season_id, [(r.user_id, r.delta) for r in top])
                        uow.track(weekly)
                        events += 1
                await uow.commit()
        return events
