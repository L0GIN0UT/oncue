"""Миграция 0004_social_sanctions: откат/накат и инварианты схемы."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from social_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gv_core.ids import uuid7
from gv_db import run_migrations
from gv_db.migrations import alembic_config
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
USER = uuid7()
ACTOR = uuid7()
INSERT = (
    "INSERT INTO sanctions (id, user_id, kind, started_at, expires_at, reason, actor_id, "
    "revoked_at, revoked_by, revoke_reason) "
    "VALUES (:id, :u, :k, :s, :e, 'r', :a, :ra, :rb, :rr)"
)


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    eng = create_async_engine(database_url)
    yield eng
    await eng.dispose()


async def _insert(engine: AsyncEngine, **over: Any) -> None:
    params: dict[str, Any] = {
        "id": uuid7(),
        "u": USER,
        "k": "online",
        "s": NOW,
        "e": NOW + timedelta(hours=1),
        "a": ACTOR,
        "ra": None,
        "rb": None,
        "rr": None,
    }
    params.update(over)
    async with engine.begin() as conn:
        await conn.execute(text(INSERT), params)


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        return set(rows.scalars())


async def test_0004_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    assert {"sanctions", "sanction_state"} <= await _tables(engine)
    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0003")
    tables = await _tables(engine)
    assert "sanctions" not in tables
    assert "sanction_state" not in tables
    assert "behaviour_counters" in tables
    await asyncio.to_thread(run_migrations, path, database_url)
    assert {"sanctions", "sanction_state"} <= await _tables(engine)


async def test_downgrade_with_journal_rows_is_refused_and_empty_one_passes(
    database_url: str, engine: AsyncEngine
) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await _insert(engine)
    with pytest.raises(RuntimeError, match="откат 0004 запрещён"):
        await asyncio.to_thread(command.downgrade, config, "0003")
    assert {"sanctions", "sanction_state"} <= await _tables(engine)  # ничего не потеряно
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM sanctions"))
    await asyncio.to_thread(command.downgrade, config, "0003")
    assert "sanctions" not in await _tables(engine)


async def test_expiry_must_be_after_start(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await _insert(engine, e=NOW)


async def test_unknown_kind_rejected(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await _insert(engine, k="ban")


async def test_revoke_fields_come_as_a_pair(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await _insert(engine, ra=NOW, rr=None)
    with pytest.raises(IntegrityError):
        await _insert(engine, ra=None, rr="снято")
    with pytest.raises(IntegrityError):
        await _insert(engine, rb=ACTOR)
    await _insert(engine, ra=NOW, rr="system:expired")  # истечение: снял не человек


async def test_one_open_record_per_user_and_kind(engine: AsyncEngine) -> None:
    await _insert(engine)
    with pytest.raises(IntegrityError):
        await _insert(engine)
    await _insert(engine, k="chat")
    await _insert(engine, ra=NOW, rr="system:replaced", rb=ACTOR)  # закрытые не мешают


async def test_state_version_is_positive(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO sanction_state (user_id, version) VALUES (:u, 1)"), {"u": USER}
        )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO sanction_state (user_id, version) VALUES (:u, 0)"),
                {"u": uuid7()},
            )
