"""Миграция 0017 и репозиторий санкций игроков: откат/накат, версии, гонка применений."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from identity_service.domain.user_sanctions import UserSanctions
from identity_service.infrastructure.db.repositories_sanctions import SqlUserSanctionRepository
from identity_service.main import DEFINITION
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


def _snapshot(version: int, *kinds: str) -> UserSanctions:
    entries = tuple(SanctionEntry(k, NOW + timedelta(hours=1)) for k in kinds)
    return UserSanctions(
        USER,
        version,
        entries,
        0,
        "account" in kinds,
        entries[0].expires_at if entries else None,
        NOW,
    )


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        return set(rows.scalars())


async def test_0017_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    assert "user_sanctions" in await _tables(engine)
    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0016")
    assert "user_sanctions" not in await _tables(engine)
    await asyncio.to_thread(run_migrations, path, database_url)
    assert "user_sanctions" in await _tables(engine)


async def test_schema_rejects_bad_version_and_mask(engine: AsyncEngine) -> None:
    insert = (
        "INSERT INTO user_sanctions (user_id, mask, account_banned, version, updated_at) "
        "VALUES (:u, :m, false, :v, now())"
    )
    for mask, version in ((0, 0), (-1, 1), (65536, 1)):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text(insert), {"u": uuid7(), "m": mask, "v": version})


async def test_apply_keeps_only_newer_versions(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        repo = SqlUserSanctionRepository(session)
        assert await repo.apply(_snapshot(2, "online"))
        assert not await repo.apply(_snapshot(2, "ranked"))
        assert not await repo.apply(_snapshot(1, "ranked"))
        assert await repo.apply(_snapshot(3, "account"))
    async with sessions() as session:
        stored = await SqlUserSanctionRepository(session).get(USER)
    assert stored is not None
    assert stored.version == 3
    assert [e.kind for e in stored.entries] == ["account"]
    assert stored.account_banned
    assert stored.banned_at(NOW)
    assert not stored.banned_at(NOW + timedelta(hours=2))  # срок вышел


async def test_concurrent_applies_end_on_highest_version(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def apply(version: int) -> None:
        async with sessions() as session, session.begin():
            await SqlUserSanctionRepository(session).apply(_snapshot(version, "online"))

    await asyncio.gather(*(apply(v) for v in (3, 1, 5, 2, 4)))
    async with sessions() as session:
        stored = await SqlUserSanctionRepository(session).get(USER)
    assert stored is not None
    assert stored.version == 5
