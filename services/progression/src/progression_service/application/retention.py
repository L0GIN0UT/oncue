"""Ретенция ``metric_marks`` и ``quest_progress`` (ADR 0062).

Журналы (``xp_ledger``, ``currency_ledger``, ``admin_actions``) append-only и здесь не чистятся.

Срок ``metric_marks`` (``retention_metric_marks_days``, по умолчанию 180): отметка нужна только
чтобы повторная доставка события с новым ``event_id`` не удвоила метрики (``ProgressTracker``);
дальше срока хранения топика событий и любого перепроигрывания повтор невозможен. Метрики
(``metric_counters``) и бейджи живут отдельно и не зависят от отметок.

Срок ``quest_progress`` (``retention_quest_progress_days``, по умолчанию 180): удаляются
строки только закрытых дневных/недельных периодов (``period_start`` старше срока); открытый
период всегда новее срока (минимум 14 дней в настройках > длины недели). Разовые задания не
удаляются никогда - строка прогресса и есть защита от повторной награды. Назначения
(``quest_assignments``) остаются: они не дают назначить период заново. Админ-статистика
«выполнено заданий» читает ``quest_progress`` и после ретенции считает только хранимый срок.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import structlog

from gv_core.clock import Clock
from progression_service.application.ports import UnitOfWorkFactory

log = structlog.get_logger(__name__)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Сколько строк удалено за запуск."""

    metric_marks: int
    quest_progress: int


class RetentionPurger:
    """Удаляет устаревшие строки пачками: своя транзакция на пачку и пауза между пачками.

    Идемпотентна: повторный запуск удаляет только оставшееся. Под advisory lock задачи relay
    запускается одной репликой.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        marks_days: int,
        quest_progress_days: int,
        batch: int,
        pause_s: float,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if batch < 1 or marks_days < 1 or quest_progress_days < 1 or pause_s < 0:
            raise ValueError("batch и сроки должны быть положительными, пауза неотрицательной")
        self._uow = uow
        self._clock = clock
        self._marks = timedelta(days=marks_days)
        self._quests = timedelta(days=quest_progress_days)
        self._batch = batch
        self._pause_s = pause_s
        self._sleep = sleep

    async def run(self) -> RetentionResult:
        """Очистить обе таблицы; вернуть число удалённых строк."""
        now = self._clock.now()
        marks_cutoff = now - self._marks
        quests_cutoff = (now - self._quests).date()

        async def purge_marks() -> int:
            async with self._uow() as uow:
                deleted = await uow.retention.purge_metric_marks(marks_cutoff, limit=self._batch)
                await uow.commit()
            return deleted

        async def purge_quests() -> int:
            async with self._uow() as uow:
                deleted = await uow.retention.purge_quest_progress(quests_cutoff, limit=self._batch)
                await uow.commit()
            return deleted

        result = RetentionResult(
            metric_marks=await self._loop(purge_marks),
            quest_progress=await self._loop(purge_quests),
        )
        log.info(
            "progression.retention_purged",
            metric_marks=result.metric_marks,
            quest_progress=result.quest_progress,
        )
        return result

    async def _loop(self, purge_batch: Callable[[], Awaitable[int]]) -> int:
        total = 0
        while True:
            deleted = await purge_batch()
            total += deleted
            if deleted < self._batch:
                return total
            await self._sleep(self._pause_s)
