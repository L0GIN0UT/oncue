"""Миграции применяются на чистую базу, совпадают с метаданными и откатываются (G2).

Партиции ``rating_history_*`` — не таблицы метаданных (их создаёт код вместе с сезоном),
поэтому при сравнении схемы пропускаются.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from rating_service.infrastructure.db.tables import HISTORY_PARTITION_PREFIX, TABLES
from rating_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration


def _include(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(HISTORY_PARTITION_PREFIX))


async def test_migrations_apply_and_match_metadata(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(sync, opts={"include_object": _include}),
                    TABLES.metadata,
                )
            )
    finally:
        await engine.dispose()
    assert diff == [], f"миграции расходятся с метаданными: {diff}"


async def test_downgrade_removes_rating_core(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    cfg = Config()
    cfg.set_main_option("script_location", str(DEFINITION.migrations_path))
    cfg.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.downgrade, cfg, "0001")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            tables = {
                r[0]
                for r in await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
    finally:
        await engine.dispose()
    assert tables.isdisjoint({"ladders", "ratings", "rating_history", "rating_history_default"})
    assert {"outbox", "inbox"} <= tables
