"""Периодические задачи collections (relay под advisory lock, ``gv_service.periodic``, K8).

* ``collections.popular.recompute`` — ``popular_score`` за окно (порциями) и очистка
  старой дневной статистики;
* ``collections.blocked.sweep`` — заблокированные единицы, которые consumer не успел убрать
  из составов первой порцией;
* очистка ``idempotency_keys`` — встроенная ``idempotency.purge`` (``gv_service``).

Все задачи идемпотентны: повтор или запуск в двух репликах ничего не дублирует.
"""

from __future__ import annotations

from collections_service.infrastructure.container import CollectionsContainer
from gv_service import PeriodicJob


def build_jobs(container: CollectionsContainer) -> list[PeriodicJob]:
    """Задачи сервиса."""
    c = container.settings.collections

    async def popular() -> None:
        await container.popular.execute()

    async def sweep() -> None:
        await container.sweep.execute()

    return [
        PeriodicJob(
            "collections.popular.recompute", c.popular.interval_s, popular, c.popular.jitter_s
        ),
        PeriodicJob("collections.blocked.sweep", c.sweep.interval_s, sweep, c.sweep.jitter_s),
    ]
