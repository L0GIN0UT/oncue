"""Миграции применяются на чистую базу и совпадают с метаданными (нет «дрейфа» схемы)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from game_service.infrastructure.db.tables import TABLES
from game_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7
from gv_db.migrations import alembic_config, run_migrations
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration


async def test_migrations_apply_and_match_metadata(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), TABLES.metadata)
            )
    finally:
        await engine.dispose()
    assert diff == [], f"миграции расходятся с метаданными: {diff}"


async def test_0004_keeps_stage2_data_and_downgrades(database_url: str) -> None:
    """0004 (expand-only): строки этапа 2 получают значения по умолчанию, откат их не теряет."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0003")
    item, now = uuid7(), datetime(2026, 10, 10, 9, tzinfo=UTC)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO catalog_items (media_item_id, domain, kind, status, version, "
                    "title) VALUES (:id, 'film', 'film', 'active', 1, 'Брат')"
                ),
                {"id": item},
            )
            await conn.execute(
                text(
                    "INSERT INTO daily_sets (mode_key, day, mode_version, status, generated_by, "
                    "created_at, updated_at) VALUES ('film.daily.frame', '2026-10-10', 1, "
                    "'scheduled', 'schedule', :now, :now)"
                ),
                {"now": now},
            )
        await asyncio.to_thread(run_migrations, path, database_url)
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT metrics, person_ids FROM catalog_items"))).one()
            period = (await conn.execute(text("SELECT period, subject_id FROM daily_sets"))).one()
        assert (row.metrics, row.person_ids) == ({}, [])
        assert (period.period, period.subject_id) == ("day", None)

        await asyncio.to_thread(command.downgrade, config, "0003")
        async with engine.connect() as conn:
            titles = (await conn.execute(text("SELECT title FROM catalog_items"))).scalars().all()
            days = (await conn.execute(text("SELECT count(*) FROM daily_sets"))).scalar()
        assert titles == ["Брат"] and days == 1
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()
