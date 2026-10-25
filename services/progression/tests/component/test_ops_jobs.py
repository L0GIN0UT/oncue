"""Задачи relay: инкрементальная сверка не теряет открытые расхождения в метрике; ретенция."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from progression_service.infrastructure.db.tables import balances
from progression_service.infrastructure.jobs import RECONCILE_JOB, RETENTION_JOB
from prometheus_client import REGISTRY
from sqlalchemy import text, update

from gv_core.ids import uuid7
from gv_service import JobResult

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py


def _sample(name: str, labels: dict[str, str] | None = None) -> float | None:
    return REGISTRY.get_sample_value(name, labels or {})


async def test_open_mismatch_stays_in_gauge_between_incremental_runs(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100]))
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            update(balances).where(balances.c.user_id == user).values(amount=balances.c.amount + 1)
        )

    assert await progression.run_job(RECONCILE_JOB) is JobResult.OK  # первый проход — полный
    assert _sample("gv_progression_ledger_mismatches") == 1.0
    assert await progression.run_job(RECONCILE_JOB) is JobResult.OK  # инкрементальный
    assert _sample("gv_progression_ledger_mismatches") == 1.0  # алерт не гаснет без исправления


async def test_retention_job_is_registered_and_deletes_old_marks(progression: Service) -> None:
    user = uuid7()
    days = progression.infra.settings.progression.retention_metric_marks_days  # type: ignore[attr-defined]
    old = progression.clock.now() - timedelta(days=days + 1)
    async with progression.infra.sessions() as session, session.begin():
        for key, at in (("old", old), ("new", progression.clock.now())):
            await session.execute(
                text("INSERT INTO metric_marks (subject_key, user_id, at) VALUES (:k, :u, :at)"),
                {"k": key, "u": user, "at": at},
            )

    assert await progression.run_job(RETENTION_JOB) is JobResult.OK

    async with progression.infra.sessions() as session:
        left = (await session.execute(text("SELECT subject_key FROM metric_marks"))).scalars().all()
    assert left == ["new"]
    assert (_sample("gv_progression_retention_deleted_total", {"table": "metric_marks"}) or 0) >= 1
