"""Периодические задачи media-pipeline (relay, под advisory lock — ``gv_service.periodic``).

* ``media.link_issuance.purge`` — журнал выдачи ссылок старше
  ``GV_MEDIA__LINK_ISSUANCE_RETENTION_DAYS`` (ADR 0012);
* ``media.profiles.backfill`` — досоздание наборов динамических профилей порциями
  ``GV_MEDIA__BACKFILL_BATCH`` (ADR 0025).

Задачи идемпотентны: повтор или запуск в двух репликах ничего не дублирует.
"""

from __future__ import annotations

from gv_service import PeriodicJob
from media_pipeline_service.infrastructure.container import MediaContainer


def build_jobs(container: MediaContainer) -> list[PeriodicJob]:
    """Задачи сервиса."""
    m = container.settings.media

    async def purge_links() -> None:
        await container.purge_links.execute()

    async def backfill() -> None:
        await container.backfill.execute()

    return [
        PeriodicJob(
            "media.link_issuance.purge", m.link_purge_interval_s, purge_links, m.link_purge_jitter_s
        ),
        PeriodicJob(
            "media.profiles.backfill", m.backfill_interval_s, backfill, m.backfill_jitter_s
        ),
    ]
