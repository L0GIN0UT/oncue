"""Серверные триггеры находок: изменения метрик двигают прогресс события (ADR 0055).

Вызывается внутри транзакции ``ProgressTracker`` (метрики событий сервисов) и транзакций колеса и
оформления - под блокировкой ``progress`` игрока. Время - момент события (``at``), не обработки:
события вне периода запуска (до начала, после конца или остановки) находки не двигают.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from progression_service.application.accrual.posting import Poster, local_day
from progression_service.application.events.unlock import PlayerRun, save_progress, unlock_find
from progression_service.application.ports import ProgressionUnitOfWork, PublishedConfig
from progression_service.domain.events_season import (
    SERVER,
    Definition,
    FindDef,
    others_done,
    parse_definition,
)
from progression_service.domain.metrics import MetricUpdate


async def apply_metric_updates(  # noqa: PLR0917 - контекст транзакции передаётся явно
    uow: ProgressionUnitOfWork,
    published: PublishedConfig,
    poster: Poster,
    user_id: UUID,
    at: datetime,
    updates: Sequence[MetricUpdate],
    now: datetime,
) -> None:
    """Применить изменения метрик к находкам запуска, идущего в момент ``at``.

    Без идущего запуска или подходящих находок - нет запросов к БД сверх одного чтения запуска.
    """
    run = await uow.events.active_at(at)
    if run is None or run.snapshot is None:
        return
    definition = parse_definition(run.snapshot)
    candidates = _candidates(definition, updates)
    if not candidates:
        return
    await poster.lock_user(user_id)
    state = await PlayerRun.load(uow, run, definition, user_id)
    day = local_day(at, published)
    for find in candidates:
        if find.key in state.unlocked:
            continue
        trigger = find.trigger
        period_key = trigger.period_key(day)
        current = state.progress_of(find.key, period_key)
        progress = current
        for update in updates:
            if trigger.accepts(update):
                progress = trigger.advance(progress, update)
        if progress == current:
            continue
        assert trigger.target is not None  # noqa: S101 - у server цель есть
        reached = progress >= trigger.target
        # Финал закрыт до остальных: прогресс копим, открываем после последней обычной находки.
        if reached and (not find.final or others_done(definition, state.unlocked)):
            await unlock_find(uow, poster, state, find, period_key, progress, now)
        else:
            await save_progress(uow, state, find, period_key, progress, now)


def _candidates(definition: Definition, updates: Sequence[MetricUpdate]) -> list[FindDef]:
    return [
        f
        for f in definition.finds
        if f.trigger.type == SERVER and any(f.trigger.accepts(u) for u in updates)
    ]
