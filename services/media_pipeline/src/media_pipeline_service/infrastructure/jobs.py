"""Периодические задачи media-pipeline (relay, под advisory lock — ``gv_service.periodic``).

* ``media.link_issuance.purge`` — журнал выдачи ссылок старше
  ``GV_MEDIA__LINK_ISSUANCE_RETENTION_DAYS`` (ADR 0012);
* ``media.profiles.backfill`` — досоздание наборов динамических профилей порциями
  ``GV_MEDIA__BACKFILL_BATCH`` (ADR 0025);
* ``media.ugc.purge`` — гарантированное удаление объектов UGC из очереди ``ugc_object_purge``
  (снятие, отклонение, замена, отмена заявки) до успеха (ADR 0048);
* ``media.ugc.orphans`` — ``p/``-объекты старше ``GV_MEDIA__UGC_ORPHAN_TTL_H`` без
  ``pending``-строки в БД.

Задачи идемпотентны: повтор или запуск в двух репликах ничего не дублирует.
"""

from __future__ import annotations

from prometheus_client import Counter

from gv_service import PeriodicJob
from media_pipeline_service.infrastructure.container import MediaContainer

LINK_ISSUANCE_PURGED = Counter(
    "gv_media_link_issuance_purged_total", "Удалённые записи журнала выдачи ссылок"
)


def build_jobs(container: MediaContainer) -> list[PeriodicJob]:
    """Задачи сервиса."""
    m = container.settings.media

    async def purge_links() -> None:
        LINK_ISSUANCE_PURGED.inc(await container.purge_links.execute())

    async def purge_ugc() -> None:
        await container.ugc.purger.sweep()

    async def sweep_ugc_orphans() -> None:
        await container.ugc.orphans.sweep()

    async def backfill() -> None:
        await container.backfill.execute()

    return [
        PeriodicJob(
            "media.link_issuance.purge", m.link_purge_interval_s, purge_links, m.link_purge_jitter_s
        ),
        PeriodicJob(
            "media.profiles.backfill", m.backfill_interval_s, backfill, m.backfill_jitter_s
        ),
        PeriodicJob("media.ugc.purge", m.ugc_purge_interval_s, purge_ugc, m.ugc_purge_jitter_s),
        PeriodicJob(
            "media.ugc.orphans", m.ugc_orphan_interval_s, sweep_ugc_orphans, m.ugc_orphan_jitter_s
        ),
    ]
