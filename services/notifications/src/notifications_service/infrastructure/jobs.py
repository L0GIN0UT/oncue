"""Периодические задачи notifications (relay, под advisory lock; идемпотентны).

* ``notifications.erased_users.purge`` - чистит tombstone удалённых игроков старше
  ``GV_NOTIFICATIONS__ERASED_RETENTION_DAYS`` (ADR 0069).
"""

from __future__ import annotations

from gv_service import Infrastructure, PeriodicJob
from notifications_service.infrastructure.container import container_for

PURGE_ERASED_JOB = "notifications.erased_users.purge"


def build_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Задачи сервиса (без БД - нет задач)."""
    if infra.sessions is None:
        return []
    c = container_for(infra)

    async def purge() -> int:
        return await c.purge_erased.execute()

    return [PeriodicJob(PURGE_ERASED_JOB, c._cfg.erased_purge_interval_s, purge, 30.0)]
