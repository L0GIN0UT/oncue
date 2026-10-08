"""Миграции применяются на чистую базу и совпадают с метаданными (нет «дрейфа» схемы)."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from identity_service.infrastructure.db.tables import TABLES
from identity_service.main import DEFINITION
from sqlalchemy.ext.asyncio import create_async_engine

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
