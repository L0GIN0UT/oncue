"""Миграции применяются на чистую базу, совпадают с метаданными и откатываются (R7).

Партиции ``match_rounds_*`` — не таблицы метаданных (их создаёт миграция и задача
``match.partitions.ensure``), поэтому при сравнении схемы пропускаются.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from match_service.infrastructure.db.repositories import ensure_partitions
from match_service.infrastructure.db.tables import ROUND_PARTITION_PREFIX, TABLES
from match_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration


def _include(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(ROUND_PARTITION_PREFIX))


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


async def test_downgrade_removes_match_core(database_url: str) -> None:
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
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            tables = set(rows.scalars().all())
    finally:
        await engine.dispose()
    assert not tables & {"matches", "match_players", "match_rounds", "match_rounds_default"}


async def test_check_constraints_and_partitions(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 12, 15, tzinfo=UTC)
        names = await ensure_partitions(sessions, now, 2)
        assert names == [
            "match_rounds_y2026m12",
            "match_rounds_y2027m01",
            "match_rounds_y2027m02",
        ]
        assert await ensure_partitions(sessions, now, 2) == names  # идемпотентно
        match_id = uuid7()
        async with sessions() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO matches (match_id, room_code, mode_key, mode_version, domain,"
                    " format, players_count, started_at, created_at)"
                    " VALUES (:m, 'K7MQ2ZXA', 'film.party.frame', 1, 'film', 'party', 3, :t, :t)"
                ),
                {"m": match_id, "t": now},
            )
            await s.execute(
                text(
                    "INSERT INTO match_rounds (match_id, round_index, played_at, media_item_id,"
                    " results) VALUES (:m, 0, :t, :i, '[]')"
                ),
                {"m": match_id, "t": now, "i": uuid7()},
            )
        async with sessions() as s:
            part = await s.scalar(text("SELECT tableoid::regclass::text FROM match_rounds"))
        assert part == "match_rounds_y2026m12"
        for bad in (
            "UPDATE matches SET finish = 'completed'",  # finish без finished_at
            "UPDATE matches SET players_count = 1",
            "UPDATE matches SET format = 'solo'",
        ):
            with pytest.raises(IntegrityError):
                async with sessions() as s, s.begin():
                    await s.execute(text(bad))
    finally:
        await engine.dispose()
