"""Периодические задачи relay: встроенные (очистка ключей идемпотентности, план этапа 2,
§2.3/§2.8 п.6) и свои — ``media.link_issuance.purge`` (хвост этапа 2, план этапа 3a M1)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.main import DEFINITION
from media_pipeline_service.settings import MediaSettings, Settings
from prometheus_client import REGISTRY
from pydantic import SecretStr
from sqlalchemy import insert, select

from gv_core import uuid7
from gv_service import Infrastructure, JobResult
from gv_testkit import FakeClock, apply_migrations, assert_idempotency_purge, run_periodic_job

pytestmark = pytest.mark.integration


async def test_idempotency_keys_are_purged(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(database_url=SecretStr(database_url), metrics_port=None)
    infra = await Infrastructure.open(settings, DEFINITION.tables, clock=FakeClock())
    try:
        await assert_idempotency_purge(DEFINITION, infra)
    finally:
        await infra.close()


async def test_link_issuance_purge_deletes_old_rows_in_batches(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=SecretStr(database_url),
        metrics_port=None,
        media=MediaSettings(link_issuance_retention_days=7, link_purge_batch=2),
    )
    clock = FakeClock()
    infra = await Infrastructure.open(settings, DEFINITION.tables, clock=clock)
    journal = TABLES.metadata.tables["link_issuance"]
    now = clock.now()
    old = [uuid7() for _ in range(5)]
    fresh = uuid7()
    rows = [(i, now - timedelta(days=8)) for i in old] + [(fresh, now - timedelta(days=6))]
    labels = {"job": "media.link_issuance.purge", "result": "ok"}
    before = REGISTRY.get_sample_value("gv_periodic_job_runs_total", labels) or 0.0
    purged_before = REGISTRY.get_sample_value("gv_media_link_issuance_purged_total") or 0.0
    try:
        assert infra.sessions is not None
        async with infra.sessions() as session, session.begin():
            for row_id, issued_at in rows:
                await session.execute(
                    insert(journal).values(
                        id=row_id,
                        asset_set_id=uuid7(),
                        level=1,
                        subject_hash="0" * 64,
                        issued_at=issued_at,
                        expires_at=issued_at + timedelta(minutes=1),
                        client_id="game",
                    )
                )
        result = await run_periodic_job(DEFINITION, "media.link_issuance.purge", infra)
        assert result is JobResult.OK
        async with infra.sessions() as session:
            left = set((await session.execute(select(journal.c.id))).scalars().all())
        assert fresh in left
        assert not left & set(old)
        after = REGISTRY.get_sample_value("gv_periodic_job_runs_total", labels)
        assert after == before + 1
        purged = REGISTRY.get_sample_value("gv_media_link_issuance_purged_total")
        assert purged == purged_before + len(old)
    finally:
        await infra.close()
