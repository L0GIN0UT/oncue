"""Миграции применяются на чистую базу, совпадают с метаданными и откатываются."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from notifications_service.infrastructure.db.tables import TABLES
from notifications_service.main import DEFINITION
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

CORE = {"notifications", "notification_preferences", "email_subscriptions", "devices"}
BROADCASTS = {"broadcasts", "broadcast_deliveries", "admin_actions", "idempotency_keys"}


async def tables_of(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
    finally:
        await engine.dispose()


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
    assert await tables_of(database_url) >= (CORE | BROADCASTS)


async def columns_of(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            found = await conn.run_sync(lambda sync: inspect(sync).get_columns(table))
            return {column["name"] for column in found}
    finally:
        await engine.dispose()


def alembic_config(path: Path, url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(path))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def test_migrations_downgrade_and_upgrade_again(database_url: str) -> None:
    """0003 и 0002 откатываются по одной и накатываются снова."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)

    config = alembic_config(DEFINITION.migrations_path, database_url)
    await asyncio.to_thread(command.downgrade, config, "0003")
    columns = await columns_of(database_url, "broadcast_deliveries")
    assert {"broadcast_id", "user_id"} <= columns
    assert not ({"token_hash", "token_expires_at"} & columns)
    await asyncio.to_thread(command.upgrade, config, "0004")
    assert {"token_hash", "token_expires_at"} <= await columns_of(
        database_url, "broadcast_deliveries"
    )
    await asyncio.to_thread(command.downgrade, config, "0002")
    after_0003 = await tables_of(database_url)
    assert not (BROADCASTS & after_0003)
    assert after_0003 >= CORE

    await asyncio.to_thread(command.downgrade, config, "0001")
    assert not (CORE & await tables_of(database_url))

    await asyncio.to_thread(command.upgrade, config, "head")
    assert await tables_of(database_url) >= (CORE | BROADCASTS)
