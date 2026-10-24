"""Миграция 0004 и проекция санкций: откат/накат, версии, гонка применений."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from match_service.domain.sanctions import SanctionsSnapshot
from match_service.infrastructure.db.sanctions import SqlSanctions
from match_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from gv_auth import SanctionEntry
from gv_core.ids import uuid7
from gv_db import run_migrations
from gv_db.migrations import alembic_config
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
USER = uuid7()


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    eng = create_async_engine(database_url)
    yield eng
    await eng.dispose()


def _snap(version: int, *kinds: str) -> SanctionsSnapshot:
    entries = tuple(SanctionEntry(k, NOW + timedelta(hours=1)) for k in kinds)
    return SanctionsSnapshot(USER, version, entries, 0, entries[0].expires_at if entries else None)


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        return set(rows.scalars())


async def test_0004_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    assert "sanctions_proj" in await _tables(engine)
    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0003")
    assert "sanctions_proj" not in await _tables(engine)
    assert "ratings_proj" in await _tables(engine)
    await asyncio.to_thread(run_migrations, path, database_url)
    assert "sanctions_proj" in await _tables(engine)


async def test_schema_rejects_bad_version_and_mask(engine: AsyncEngine) -> None:
    insert = (
        "INSERT INTO sanctions_proj (user_id, mask, version, updated_at) VALUES (:u, :m, :v, now())"
    )
    for mask, version in ((0, 0), (-1, 1), (65536, 1)):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text(insert), {"u": uuid7(), "m": mask, "v": version})


async def test_only_newer_versions_are_applied_and_read_back(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        assert await SqlSanctions.apply(session, _snap(2, "online"), NOW)
        assert not await SqlSanctions.apply(session, _snap(2, "ranked"), NOW)
        assert not await SqlSanctions.apply(session, _snap(1, "ranked"), NOW)
        assert await SqlSanctions.apply(session, _snap(3, "chat"), NOW)
    stored = await SqlSanctions(sessions).get(USER)
    assert stored is not None
    assert stored.version == 3
    assert [e.kind for e in stored.entries] == ["chat"]
    assert await SqlSanctions(sessions).get(uuid7()) is None


async def test_concurrent_applies_end_on_highest_version(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def apply(version: int) -> None:
        async with sessions() as session, session.begin():
            await SqlSanctions.apply(session, _snap(version, "online"), NOW)

    await asyncio.gather(*(apply(v) for v in (3, 1, 5, 2, 4)))
    stored = await SqlSanctions(sessions).get(USER)
    assert stored is not None and stored.version == 5
