"""SQL-адаптер проекции ladder-ов: запись защищена версией на уровне SQL (ADR 0037)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from game_service.domain.projection import LadderProj
from game_service.infrastructure.db.repositories import SqlLadderRepository
from game_service.main import DEFINITION
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

AT = datetime(2026, 10, 23, tzinfo=UTC)


def proj(version: int, domain: str = "film") -> LadderProj:
    return LadderProj(ladder="film", domain=domain, version=version, updated_at=AT)


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def save(sessions: async_sessionmaker[AsyncSession], value: LadderProj) -> None:
    async with sessions() as session, session.begin():
        await SqlLadderRepository(session).save(value)


async def current(sessions: async_sessionmaker[AsyncSession]) -> LadderProj | None:
    async with sessions() as session:
        return await SqlLadderRepository(session).get("film")


async def test_stale_write_does_not_overwrite_newer(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await save(sessions, proj(3))
    await save(sessions, proj(2, domain="music"))  # без where перезаписала бы
    await save(sessions, proj(3, domain="music"))  # повтор версии

    found = await current(sessions)
    assert found is not None and (found.version, found.domain) == (3, "film")


async def test_newer_write_replaces(sessions: async_sessionmaker[AsyncSession]) -> None:
    await save(sessions, proj(1))
    await save(sessions, proj(2))

    found = await current(sessions)
    assert found is not None and found.version == 2


async def test_concurrent_writes_keep_highest_version(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await save(sessions, proj(1))

    await asyncio.gather(*(save(sessions, proj(v)) for v in (5, 2, 9, 4, 7)))

    found = await current(sessions)
    assert found is not None and found.version == 9
