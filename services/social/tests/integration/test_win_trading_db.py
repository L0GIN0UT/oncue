"""Миграция 0007 и SQL-хранилище win-trading на реальном Postgres."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic import command
from social_service.domain.win_trading import MatchFact, Signal, pair_of
from social_service.infrastructure.db.repositories_win_trading import SqlWinTradingStore
from social_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_db import run_migrations
from gv_db.migrations import alembic_config
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    eng = create_async_engine(database_url)
    yield eng
    await eng.dispose()


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'win_trading_%'")
        )
        return {r.tablename for r in rows}


def _fact(low: UUID, high: UUID, minute: int, winner: UUID | None = None) -> MatchFact:
    return MatchFact(uuid7(), low, high, winner, None, T0 + timedelta(minutes=minute))


def _signal(key: str, user: UUID, other: UUID | None, minute: int, count: int = 5) -> Signal:
    return Signal(
        key=key,
        kind="pair_repeat",
        user_id=user,
        other_user_id=other,
        count=count,
        streak=2,
        severity="medium",
        window_s=3600,
        first_at=T0,
        last_at=T0 + timedelta(minutes=minute),
        details={"wins": {}},
    )


async def test_0007_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    assert await _tables(engine) == {"win_trading_matches", "win_trading_signals"}
    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0006")
    assert await _tables(engine) == set()
    await asyncio.to_thread(run_migrations, path, database_url)
    assert len(await _tables(engine)) == 2


async def test_facts_are_idempotent_ordered_and_pair_is_checked(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    low, high = pair_of(uuid7(), uuid7())
    first, second = _fact(low, high, 0, low), _fact(low, high, 5, high)
    async with factory() as session, session.begin():
        store = SqlWinTradingStore(session)
        assert await store.add_fact(first)
        assert not await store.add_fact(first)
        assert await store.add_fact(second)
    async with factory() as session:
        got = await SqlWinTradingStore(session).pair_facts(low, high, T0)
        assert [f.match_id for f in got] == [second.match_id, first.match_id]  # новые первыми
        assert await SqlWinTradingStore(session).pair_facts(
            low, high, T0 + timedelta(minutes=1)
        ) == [second]
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            await SqlWinTradingStore(session).add_fact(_fact(high, low, 9))


async def test_signals_upsert_filter_page_and_delete(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    a, b, c = uuid7(), uuid7(), uuid7()
    async with factory() as session, session.begin():
        store = SqlWinTradingStore(session)
        await store.sync_signal(f"k:{a}:{b}", _signal(f"k:{a}:{b}", a, b, 1, count=5), T0)
        await store.sync_signal(f"k:{a}:{b}", _signal(f"k:{a}:{b}", a, b, 2, count=7), T0)
        await store.sync_signal(f"k:{b}:{c}", _signal(f"k:{b}:{c}", b, c, 3), T0)
    async with factory() as session:
        store = SqlWinTradingStore(session)
        everything = await store.signals(
            kind=None, severity=None, user_id=None, after=None, limit=10
        )
        assert [s.user_id for s in everything] == [b, a]  # свежие первыми
        assert everything[1].count == 7  # upsert, не дубль
        mine = await store.signals(kind=None, severity=None, user_id=c, after=None, limit=10)
        assert [s.user_id for s in mine] == [b]  # игрок на любой стороне
        page = await store.signals(kind=None, severity=None, user_id=None, after=None, limit=1)
        rest = await store.signals(
            kind=None,
            severity=None,
            user_id=None,
            after=(page[0].last_at, page[0].key),
            limit=10,
        )
        assert [s.user_id for s in rest] == [a]
        assert (
            await store.signals(
                kind="forfeit_window", severity=None, user_id=None, after=None, limit=5
            )
            == []
        )
    async with factory() as session, session.begin():
        store = SqlWinTradingStore(session)
        await store.sync_signal(f"k:{a}:{b}", None, T0)
        await store.delete_user(c)
    async with factory() as session:
        left = await SqlWinTradingStore(session).signals(
            kind=None, severity=None, user_id=a, after=None, limit=10
        )
    assert left == []
