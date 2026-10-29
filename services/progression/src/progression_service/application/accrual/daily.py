"""Награда за дейлик по ``game.DailyCompleted`` и серия по дням (ADR 0042; R2).

Раунды дейлика (``GameFinished`` с ``day``) опыта не дают — только эта награда, поэтому она не
двоится. Идемпотентность — ``op_id = daily:{user}:{domain}:{day}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import config_at
from progression_service.application.accrual.posting import Poster
from progression_service.application.accrual.solo import AccrualResult
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.domain import experience, wallet

log = structlog.get_logger(__name__)

DAILY_REASON = "daily"
_DAILY_OP_PARTS = 4


def daily_op_id(user_id: UUID, domain: str, day: date) -> str:
    """``op_id`` награды за дейлик: один на игрока, домен и день."""
    return f"daily:{user_id}:{domain}:{day.isoformat()}"


def parse_daily_op_id(op_id: str) -> tuple[str, date] | None:
    """Домен и день из ``op_id`` награды за дейлик; ``None`` — это не она."""
    parts = op_id.split(":")
    if len(parts) != _DAILY_OP_PARTS or parts[0] != "daily" or not parts[2]:
        return None
    try:
        return parts[2], date.fromisoformat(parts[3])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CompletedDaily:
    """Данные ``DailyCompleted``.

    Attributes:
        occurred_at: Время события (``occurred_at`` конверта: в payload времени нет) — по нему
            выбирается версия конфига.
    """

    session_id: UUID
    user_id: UUID
    domain: str
    day: date
    occurred_at: datetime


class AccrueDaily:
    """Серия, опыт и Версы за дейлик одной транзакцией."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, daily: CompletedDaily) -> AccrualResult:
        """Засчитать день в серию и начислить награду.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига (повторимо).
        """
        async with self._uow() as uow:
            published = await config_at(uow, daily.occurred_at)
            cfg = published.config
            poster = Poster(uow, published, self._clock.now())
            await poster.lock_user(daily.user_id)
            streak = await uow.streaks.lock(daily.user_id, daily.domain)
            # Опоздавшее событие за день раньше последнего засчитанного серию не меняет и бонус
            # длинной серии последнего дня не получает: награда как за серию 1.
            late = streak.last_day is not None and daily.day < streak.last_day
            streak, changed = streak.advance(daily.day)
            if changed:
                await uow.streaks.save(daily.user_id, daily.domain, streak)
            reward_streak = 1 if late else streak.current
            op_id = daily_op_id(daily.user_id, daily.domain, daily.day)
            ref = str(daily.session_id)
            xp = await poster.xp(
                daily.user_id,
                op_id=op_id,
                base_amount=experience.daily_xp(cfg.xp.daily, reward_streak),
                reason=DAILY_REASON,
                source_ref=ref,
            )
            verses = await poster.verses(
                daily.user_id,
                op_id=op_id,
                amount=wallet.daily_verses(cfg.verses),
                reason=DAILY_REASON,
                source_ref=ref,
            )
            await uow.commit()
        log.info(
            "progression.daily_accrued",
            session_id=ref,
            streak=streak.current,
            late=late,
            xp=xp,
            verses=verses,
        )
        return AccrualResult.ACCRUED
