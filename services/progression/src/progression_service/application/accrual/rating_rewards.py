"""Награды рейтинга: топ недели и итог сезона по сеткам мест (ADR 0043).

Идемпотентность двойная: отметка ``reward_periods`` по ``(period, user)`` и ``UNIQUE(op_id)``
проводки. Пачки сезона приходят в любом порядке и по отдельности — каждая награждает свои места
и не ждёт остальных.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import AccrualContractError, config_at
from progression_service.application.accrual.posting import Poster
from progression_service.application.accrual.solo import AccrualResult
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.domain.config import Band, VersesConfig
from progression_service.domain.wallet import grid_amount

log = structlog.get_logger(__name__)

WEEKLY_REASON = "weekly_top"
SEASON_REASON = "season"


@dataclass(frozen=True, slots=True)
class PlacedUser:
    """Игрок и его место."""

    user_id: UUID
    place: int


@dataclass(frozen=True, slots=True)
class WeeklyTop:
    """Данные ``rating.WeeklyTopFinalized``.

    Attributes:
        closed_at: Момент закрытия недели (``occurred_at`` конверта: в payload времени нет).
    """

    ladder: str
    week_start: date
    top: tuple[PlacedUser, ...]
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class SeasonStandings:
    """Пачка ``rating.SeasonStandingsFinalized`` (``finished_at`` — конец сезона)."""

    season_id: UUID
    ladder: str
    chunk: int
    entries: tuple[PlacedUser, ...]
    finished_at: datetime


class _RatingRewards:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def _grant(
        self,
        entries: tuple[PlacedUser, ...],
        *,
        at: datetime,
        period_key: str,
        op_prefix: str,
        reason: str,
        source_ref: str,
        bands_of: Callable[[VersesConfig], tuple[Band, ...]],
    ) -> int:
        """Выдать награды по сетке; вернуть число новых проводок."""
        ids = [e.user_id for e in entries]
        if len(set(ids)) != len(ids):
            raise AccrualContractError("игрок дважды в одной таблице мест")
        granted = 0
        async with self._uow() as uow:
            published = await config_at(uow, at)
            now = self._clock.now()
            poster = Poster(uow, published, now)
            bands = bands_of(published.config.verses)
            ordered = sorted(entries, key=lambda e: e.user_id)
            # Единый порядок блокировок (как в матче): сначала progress игроков по возрастанию
            # user_id, и только потом отметки периода и балансы.
            for entry in ordered:
                await poster.lock_user(entry.user_id)
            for entry in ordered:
                if not await uow.rewards.mark(period_key, entry.user_id, now):
                    continue
                amount = grid_amount(bands, entry.place)
                if await poster.verses(
                    entry.user_id,
                    op_id=f"{op_prefix}:{entry.user_id}",
                    amount=amount,
                    reason=reason,
                    source_ref=source_ref,
                ):
                    granted += 1
            await uow.commit()
        return granted


class AccrueWeeklyTop(_RatingRewards):
    """Версы за топ недели (сетка ``verses.weekly_top``, топ-100)."""

    async def execute(self, top: WeeklyTop) -> AccrualResult:
        """Наградить игроков закрытой недели.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига (повторимо).
        """
        key = f"weekly_top:{top.ladder}:{top.week_start.isoformat()}"
        granted = await self._grant(
            top.top,
            at=top.closed_at,
            period_key=key,
            op_prefix=key,
            reason=WEEKLY_REASON,
            source_ref=key,
            bands_of=lambda v: v.weekly_top,
        )
        log.info("progression.weekly_top_accrued", ladder=top.ladder, granted=granted)
        return AccrualResult.ACCRUED


class AccrueSeason(_RatingRewards):
    """Версы за итог сезона (сетка ``verses.season``, топ-1000), пачками."""

    async def execute(self, standings: SeasonStandings) -> AccrualResult:
        """Наградить игроков пачки.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига (повторимо).
        """
        key = f"season:{standings.season_id}"
        granted = await self._grant(
            standings.entries,
            at=standings.finished_at,
            period_key=key,
            op_prefix=key,
            reason=SEASON_REASON,
            source_ref=key,
            bands_of=lambda v: v.season,
        )
        log.info(
            "progression.season_accrued",
            season_id=str(standings.season_id),
            chunk=standings.chunk,
            granted=granted,
        )
        return AccrualResult.ACCRUED
