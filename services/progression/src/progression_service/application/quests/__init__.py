"""Задания: назначение снимком на период и чтение (ADR 0042, 0044; R1, R8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from gv_core.clock import Clock
from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster, local_day
from progression_service.application.ports import (
    ProgressionUnitOfWork,
    PublishedConfig,
    UnitOfWorkFactory,
)
from progression_service.application.ports.rewards import AssignedQuest
from progression_service.domain import quests
from progression_service.domain.quests import ONE_TIME, QuestSnapshot


async def ensure_assigned(
    uow: ProgressionUnitOfWork,
    published: PublishedConfig,
    user_id: UUID,
    day: date,
    now: datetime,
) -> list[AssignedQuest]:
    """Задания игрока на локальные сутки ``day``: дневные, недельные и разовые.

    Недостающие назначаются снимком по конфигу ``published`` (для события - на ``finished_at``,
    R1); уже назначенные не меняются, поэтому смена конфига действует со следующего периода (R8).
    Строки прогресса берутся ``FOR UPDATE``. Вызывать после ``Poster.lock_user``: блокировка
    игрока сериализует первое назначение.
    """
    cfg = published.config.quests
    missing: list[QuestSnapshot] = []
    for period, count in ((quests.DAILY, cfg.daily_count), (quests.WEEKLY, cfg.weekly_count)):
        start = quests.period_start(period, day)
        if not await uow.quests.assigned(user_id, period, start):
            chosen = quests.select_quests(
                cfg.pool(period),
                count,
                user_id=user_id,
                period=period,
                start=start,
                version=published.version,
            )
            missing += [QuestSnapshot.of(q, start, published.version) for q in chosen]
    have = {a.snapshot.key for a in await uow.quests.assigned(user_id, ONE_TIME, None)}
    missing += [
        QuestSnapshot.of(q, day, published.version) for q in cfg.pool(ONE_TIME) if q.key not in have
    ]
    if missing:
        await uow.quests.add(user_id, missing, now)
    found: list[AssignedQuest] = []
    for period in (quests.DAILY, quests.WEEKLY):
        found += await uow.quests.assigned(
            user_id, period, quests.period_start(period, day), lock=True
        )
    return found + await uow.quests.assigned(user_id, ONE_TIME, None, lock=True)


@dataclass(frozen=True, slots=True)
class PeriodQuests:
    """Задания одного периода для ответа API."""

    period: str
    period_start: date
    period_end: date | None
    items: tuple[AssignedQuest, ...]


@dataclass(frozen=True, slots=True)
class MyQuestsView:
    """Дневные, недельные и разовые задания игрока."""

    daily: PeriodQuests
    weekly: PeriodQuests
    one_time: PeriodQuests


class QuestQueries:
    """Чтение заданий игрока (первое обращение за период назначает набор и сохраняет его)."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def my_quests(self, user_id: UUID) -> MyQuestsView:
        """Задания игрока на сегодня.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига.
        """
        async with self._uow() as uow:
            published = await published_config(uow)
            now = self._clock.now()
            poster = Poster(uow, published, now)
            await poster.lock_user(user_id)
            day = local_day(now, published)
            found = await ensure_assigned(uow, published, user_id, day, now)
            await uow.commit()

        def of(period: str) -> PeriodQuests:
            items = tuple(a for a in found if a.snapshot.period == period)
            start = quests.period_start(period, day)
            if period == ONE_TIME and items:
                start = min(a.snapshot.period_start for a in items)
            return PeriodQuests(period, start, quests.period_end(period, start), items)

        return MyQuestsView(of(quests.DAILY), of(quests.WEEKLY), of(ONE_TIME))
