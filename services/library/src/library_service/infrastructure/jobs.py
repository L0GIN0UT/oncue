"""Периодические задачи library (relay, под advisory lock ``gv_service.periodic``).

* ``library.users.purge`` — дочистка медиатеки удалённых пользователей порциями
  (``identity.UserDeleted``); повтор и запуск в двух репликах ничего не дублируют.
"""

from __future__ import annotations

from gv_service import PeriodicJob
from library_service.infrastructure.container import LibraryContainer


def build_jobs(container: LibraryContainer) -> list[PeriodicJob]:
    """Задачи сервиса."""
    lib = container.settings.library

    async def purge() -> None:
        await container.purge.run()

    return [PeriodicJob("library.users.purge", lib.purge_interval_s, purge, lib.purge_jitter_s)]
