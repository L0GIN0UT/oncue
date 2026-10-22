"""Периодические задачи match (relay, под advisory lock): партиции ``match_rounds`` вперёд."""

from __future__ import annotations

from gv_service import PeriodicJob
from match_service.infrastructure.container import MatchContainer
from match_service.infrastructure.db.repositories import ensure_partitions

PARTITIONS_JOB = "match.partitions.ensure"


def build_jobs(container: MatchContainer) -> list[PeriodicJob]:
    """Задачи сервиса (без БД — нет задач)."""
    infra = container.infra
    sessions = infra.sessions
    if sessions is None:
        return []
    m = container.settings.match

    async def partitions() -> None:
        await ensure_partitions(sessions, infra.clock.now(), m.partitions_ahead_months)

    return [
        PeriodicJob(
            name=PARTITIONS_JOB,
            interval_s=m.partitions_interval_s,
            run=partitions,
            jitter_s=min(60.0, m.partitions_interval_s / 10),
        )
    ]
