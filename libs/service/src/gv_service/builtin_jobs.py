"""Встроенные периодические задачи всех сервисов с БД (план этапа 2, §2.3).

* ``outbox.purge_published`` — опубликованные события старше ``GV_RETENTION__OUTBOX_DAYS``;
* ``inbox.purge`` — отметки обработанных событий старше того же срока;
* ``idempotency.purge`` — ключи старше ``GV_IDEMPOTENCY__RETENTION_H``, если в метаданных
  сервиса есть таблица ``idempotency_keys``.

Так таблицы перестают расти без правок в сервисах (хвост этапа 1).
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from gv_db.idempotency import IDEMPOTENCY_TABLE, SqlIdempotencyStore
from gv_messaging.inbox import purge_inbox
from gv_messaging.outbox import purge_published_outbox
from gv_service.definition import Infrastructure, ServiceDefinition
from gv_service.periodic import PeriodicJob

log = structlog.get_logger(__name__)


def builtin_periodic_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Встроенные задачи очистки; пусто, если у сервиса нет БД."""
    sessions, tables = infra.sessions, infra.tables
    if sessions is None or tables is None:
        return []
    settings, clock = infra.settings, infra.clock
    retention = timedelta(days=settings.retention.outbox_days)
    interval, jitter = settings.retention.purge_interval_s, settings.retention.purge_jitter_s

    async def purge_outbox() -> None:
        async with sessions() as session, session.begin():
            deleted = await purge_published_outbox(session, tables.outbox, clock.now() - retention)
        log.info("outbox.purged", deleted=deleted)

    async def purge_inbox_marks() -> None:
        async with sessions() as session, session.begin():
            deleted = await purge_inbox(session, tables.inbox, clock.now() - retention)
        log.info("inbox.purged", deleted=deleted)

    jobs = [
        PeriodicJob("outbox.purge_published", interval, purge_outbox, jitter),
        PeriodicJob("inbox.purge", interval, purge_inbox_marks, jitter),
    ]
    idempotency = tables.metadata.tables.get(IDEMPOTENCY_TABLE)
    if idempotency is not None:
        keys_retention = timedelta(hours=settings.idempotency.retention_h)

        async def purge_idempotency() -> None:
            async with sessions() as session, session.begin():
                store = SqlIdempotencyStore(session, idempotency, clock)
                deleted = await store.purge_older_than(clock.now() - keys_retention)
            log.info("idempotency.purged", deleted=deleted)

        jobs.append(PeriodicJob("idempotency.purge", interval, purge_idempotency, jitter))
    return jobs


def collect_periodic_jobs(
    definition: ServiceDefinition, infra: Infrastructure
) -> list[PeriodicJob]:
    """Все задачи процесса relay: встроенные + ``definition.build_periodic_jobs``."""
    jobs = builtin_periodic_jobs(infra)
    if definition.build_periodic_jobs is not None:
        jobs += definition.build_periodic_jobs(infra)
    return jobs
