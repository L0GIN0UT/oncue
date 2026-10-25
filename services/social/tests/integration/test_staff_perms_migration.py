"""Миграция 0006_staff_perms_proj: накат/откат и версионная запись проекции."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from alembic import command
from social_service.infrastructure.db.repositories_moderation import SqlStaffPerms
from social_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_db import run_migrations
from gv_db.migrations import alembic_config
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    eng = create_async_engine(database_url)
    yield eng
    await eng.dispose()


async def _has_table(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='staff_perms_proj'"
            )
        )
        return rows.first() is not None


async def test_0006_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    assert await _has_table(engine)
    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0005")
    assert not await _has_table(engine)
    await asyncio.to_thread(run_migrations, path, database_url)
    assert await _has_table(engine)


async def test_projection_applies_only_newer_versions(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user = uuid7()
    async with factory() as session, session.begin():
        repo = SqlStaffPerms(session)
        await repo.apply(user, frozenset({"a.b"}), 2)
        await repo.apply(user, frozenset({"x.y"}), 1)
        await repo.apply(user, frozenset({"x.y"}), 2)
    async with factory() as session:
        got = (
            await session.execute(
                text("SELECT perms, version FROM staff_perms_proj WHERE user_id=:u"), {"u": user}
            )
        ).one()
    assert (sorted(got.perms), got.version) == (["a.b"], 2)
    async with factory() as session, session.begin():
        await SqlStaffPerms(session).delete(user)
    async with factory() as session:
        left = await session.scalar(text("SELECT count(*) FROM staff_perms_proj"))
    assert left == 0
