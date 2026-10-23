"""Миграции применяются на чистую базу и совпадают с метаданными (нет «дрейфа» схемы)."""

from __future__ import annotations

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from progression_service.infrastructure.db.tables import TABLES
from progression_service.main import DEFINITION
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


async def test_downgrade_is_honest_and_reapplies(database_url: str) -> None:
    """Откат ``0002`` пустой БД убирает все её объекты (и функцию триггера), upgrade повторим."""
    import asyncio

    from alembic import command
    from sqlalchemy import text

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    config = alembic_config(DEFINITION.migrations_path, database_url)
    await apply_migrations(database_url, DEFINITION.migrations_path)
    await asyncio.to_thread(command.downgrade, config, "0001")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            tables = {
                r[0]
                for r in await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
            functions = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_proc "
                        "WHERE proname = 'progression_ledger_forbid_mutation'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert tables == {"outbox", "inbox", "alembic_version"}
    assert functions == 0

    await apply_migrations(database_url, DEFINITION.migrations_path)


@pytest.mark.parametrize("table", ["xp_ledger", "currency_ledger"])
async def test_downgrade_with_ledger_rows_is_refused(
    progression: Any, database_url: str, table: str
) -> None:
    """Финансовые данные не удаляются молча: откат с проводками в любом журнале прерывается."""
    import asyncio

    from alembic import command
    from sqlalchemy import text

    from gv_core.ids import uuid7
    from gv_db.migrations import alembic_config

    user = uuid7()
    if table == "xp_ledger":  # только опыт: Версы соло упираются в нулевой потолок
        await progression.publish_config(verses__solo_daily_cap=0)
        await progression.deliver(progression.game_finished(user, [100]))
        assert await progression.verse_rows() == 0
    else:  # только Версы: награда недели
        await progression.publish_config()
        await progression.deliver(progression.weekly_top([(user, 1)]))
        assert await progression.xp_rows() == 0

    assert DEFINITION.migrations_path is not None
    config = alembic_config(DEFINITION.migrations_path, database_url)
    with pytest.raises(Exception, match=table):
        await asyncio.to_thread(command.downgrade, config, "0001")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608 — имя из parametrize
            version = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
    finally:
        await engine.dispose()
    assert rows == 1
    assert version == "0002"


async def test_downgrade_0003_drops_reward_tables_only_and_reapplies(database_url: str) -> None:
    """Откат ``0003`` убирает только таблицы P2 (денежных журналов в нём нет), ядро P1 цело."""
    import asyncio

    from alembic import command
    from sqlalchemy import text

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    config = alembic_config(DEFINITION.migrations_path, database_url)
    await apply_migrations(database_url, DEFINITION.migrations_path)
    await asyncio.to_thread(command.downgrade, config, "0002")

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
    reward_tables = {
        "quest_assignments",
        "quest_progress",
        "badges_unlocked",
        "metric_counters",
        "metric_marks",
        "wheel_spins",
        "boosts",
        "boost_spends",
        "grants_log",
    }
    assert not tables & reward_tables
    assert {"xp_ledger", "currency_ledger", "balances", "progress"} <= tables

    await apply_migrations(database_url, DEFINITION.migrations_path)
