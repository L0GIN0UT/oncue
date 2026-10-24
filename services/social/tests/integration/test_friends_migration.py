"""Миграция 0002_social_friends: откат/накат и инварианты схемы."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
LOW = "00000000-0000-0000-0000-000000000001"
HIGH = "00000000-0000-0000-0000-000000000002"
SHA = "a" * 64


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    eng = create_async_engine(database_url)
    yield eng
    await eng.dispose()


async def tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        return set(rows.scalars())


async def test_0002_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    assert {"friendships", "friend_requests", "friend_codes", "blocks", "privacy_settings"} <= (
        await tables(engine)
    )

    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0001")
    assert await tables(engine) == {"outbox", "inbox", "alembic_version"}

    await asyncio.to_thread(run_migrations, path, database_url)
    assert {"config_versions", "users_proj", "in_match_proj", "idempotency_keys"} <= (
        await tables(engine)
    )


async def exec_sql(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def test_friendship_pair_must_be_ordered_and_unique(engine: AsyncEngine) -> None:
    insert = "INSERT INTO friendships (user_a, user_b, created_at) VALUES (:a, :b, :t)"
    with pytest.raises(IntegrityError):
        await exec_sql(engine, insert, a=HIGH, b=LOW, t=NOW)
    await exec_sql(engine, insert, a=LOW, b=HIGH, t=NOW)
    with pytest.raises(IntegrityError):
        await exec_sql(engine, insert, a=LOW, b=HIGH, t=NOW)


async def test_only_one_pending_request_per_pair_in_any_direction(engine: AsyncEngine) -> None:
    insert = (
        "INSERT INTO friend_requests (id, from_user_id, to_user_id, user_a, user_b, status, "
        "created_at) VALUES (:id, :f, :t, :a, :b, :s, :ts)"
    )
    await exec_sql(engine, insert, id=uuid7(), f=LOW, t=HIGH, a=LOW, b=HIGH, s="pending", ts=NOW)
    with pytest.raises(IntegrityError):
        await exec_sql(
            engine, insert, id=uuid7(), f=HIGH, t=LOW, a=LOW, b=HIGH, s="pending", ts=NOW
        )
    # закрытые заявки пару не занимают
    await exec_sql(engine, insert, id=uuid7(), f=HIGH, t=LOW, a=LOW, b=HIGH, s="declined", ts=NOW)


async def test_one_draft_and_one_published_config(engine: AsyncEngine) -> None:
    insert = (
        "INSERT INTO config_versions (version, status, body, body_sha256, created_by, created_at, "
        "published_at) VALUES (:v, :s, '{}'::jsonb, :h, :u, :t, :p)"
    )
    base = {"h": SHA, "u": LOW, "t": NOW}
    await exec_sql(engine, insert, v=1, s="published", p=NOW, **base)
    await exec_sql(engine, insert, v=2, s="draft", p=None, **base)
    with pytest.raises(IntegrityError):
        await exec_sql(engine, insert, v=3, s="published", p=NOW, **base)
    with pytest.raises(IntegrityError):
        await exec_sql(engine, insert, v=3, s="draft", p=None, **base)
    await exec_sql(engine, insert, v=3, s="archived", p=NOW, **base)


async def test_friend_code_is_unique(engine: AsyncEngine) -> None:
    insert = "INSERT INTO friend_codes (user_id, code, rotated_at) VALUES (:u, :c, :t)"
    await exec_sql(engine, insert, u=LOW, c="K7M2QX9P", t=NOW)
    with pytest.raises(IntegrityError):
        await exec_sql(engine, insert, u=HIGH, c="K7M2QX9P", t=NOW)


async def test_block_self_and_unknown_policy_are_rejected(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await exec_sql(engine, "INSERT INTO blocks VALUES (:a, :a, :t)", a=LOW, t=NOW)
    with pytest.raises(IntegrityError):
        await exec_sql(
            engine,
            "INSERT INTO privacy_settings (user_id, friend_requests, updated_at) "
            "VALUES (:u, 'some', :t)",
            u=LOW,
            t=NOW,
        )
