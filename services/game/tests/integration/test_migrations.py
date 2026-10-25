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
from sqlalchemy import TextClause, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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


_ASSET_SQL = text(
    "INSERT INTO clue_assets (media_item_id, clue_type, params_hash, asset_set_id, "
    "levels_count, status, version) VALUES (:item, 'emoji', :hash, :set, 6, 'ready', 1)"
)
_SCOPED_ASSET_SQL = text(
    "INSERT INTO clue_assets (media_item_id, clue_type, params_hash, asset_set_id, "
    "levels_count, status, version, scope_id) "
    "VALUES (:item, 'emoji', :hash, :set, 6, 'ready', 1, :scope)"
)


def _asset_sql(*, scoped: bool) -> TextClause:
    return _SCOPED_ASSET_SQL if scoped else _ASSET_SQL


async def test_0005_keeps_stage3a_assets_and_downgrades(database_url: str) -> None:
    """0005 (expand-only): ассеты 3a становятся каталожными (``scope_id`` NULL), рядом
    заводится набор сборника; каталожный дубль запрещён, откат убирает только scoped."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0004")
    item, catalog_set, hash_ = uuid7(), uuid7(), "a" * 64
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                _asset_sql(scoped=False), {"item": item, "hash": hash_, "set": catalog_set}
            )
        await asyncio.to_thread(run_migrations, path, database_url)
        async with engine.begin() as conn:
            row = (await conn.execute(text("SELECT asset_set_id, scope_id FROM clue_assets"))).one()
            assert (row.asset_set_id, row.scope_id) == (catalog_set, None)
            await conn.execute(
                _asset_sql(scoped=True),
                {"item": item, "hash": hash_, "set": uuid7(), "scope": uuid7()},
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    _asset_sql(scoped=False), {"item": item, "hash": hash_, "set": uuid7()}
                )

        await asyncio.to_thread(command.downgrade, config, "0004")
        async with engine.connect() as conn:
            sets = (await conn.execute(text("SELECT asset_set_id FROM clue_assets"))).scalars()
            assert list(sets) == [catalog_set]
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


_TRGM_INDEX = "ix_catalog_items_title_trgm"
_INDEX_SQL = text("SELECT indexdef FROM pg_indexes WHERE indexname = :name")


async def _index_def(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        return (await conn.execute(_INDEX_SQL, {"name": _TRGM_INDEX})).scalar()


async def test_0007_trigram_index_serves_ilike_and_downgrades(database_url: str) -> None:
    """0007: GIN ``gin_trgm_ops`` по ``title`` обслуживает ``ILIKE '%…%'`` админ-поиска проекции;
    откат снимает индекс, повторный накат идемпотентен (``IF NOT EXISTS``)."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url)
    engine = create_async_engine(database_url)
    try:
        definition = await _index_def(engine)
        assert definition is not None
        assert "gin" in definition and "gin_trgm_ops" in definition
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO catalog_items (media_item_id, domain, kind, status, version, "
                    "title) SELECT gen_random_uuid(), 'film', 'film', 'active', 1, "
                    "'Фильм ' || md5(g::text) FROM generate_series(1, 5000) g"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO catalog_items (media_item_id, domain, kind, status, version, "
                    "title) VALUES (gen_random_uuid(), 'film', 'film', 'active', 1, 'Брат 2')"
                )
            )
            await conn.execute(text("ANALYZE catalog_items"))
        async with engine.connect() as conn:
            # Планировщик на крошечной таблице любит seq scan: проверяем применимость индекса.
            await conn.execute(text("SET enable_seqscan = off"))
            plan = "\n".join(
                (
                    await conn.execute(
                        text("EXPLAIN SELECT * FROM catalog_items WHERE title ILIKE '%брат 2%'")
                    )
                ).scalars()
            )
            found = (
                await conn.execute(
                    text("SELECT title FROM catalog_items WHERE title ILIKE '%БРАТ 2%'")
                )
            ).scalars()
            assert list(found) == ["Брат 2"]
        assert _TRGM_INDEX in plan, plan

        await asyncio.to_thread(command.downgrade, config, "0006")
        assert await _index_def(engine) is None
        await asyncio.to_thread(run_migrations, path, database_url)
        assert await _index_def(engine) is not None
    finally:
        await engine.dispose()


async def test_0007_skips_index_without_pg_trgm(database_url: str) -> None:
    """Нет расширения ``pg_trgm`` (создаёт суперпользователь при инициализации БД) — миграция
    не падает и индекс не создаёт: поиск работает, только медленнее."""
    path = DEFINITION.migrations_path
    assert path is not None
    await asyncio.to_thread(run_migrations, path, database_url, "0006")
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DROP EXTENSION pg_trgm"))
        await asyncio.to_thread(run_migrations, path, database_url)
        assert await _index_def(engine) is None
    finally:
        await engine.dispose()
