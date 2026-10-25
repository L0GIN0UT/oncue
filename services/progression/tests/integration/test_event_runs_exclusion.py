"""Ограничение БД на пересечение запусков событий (миграция ``0011``; ADR 0054, 0071).

Advisory-lock и проверка в транзакции остаются первой линией (понятный 409); здесь проверяется
последняя - сама схема отвергает пересечение даже при прямой записи в обход приложения.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from progression_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gv_core.ids import uuid7
from gv_db.migrations import alembic_config

pytestmark = pytest.mark.integration

CONSTRAINT = "ex_event_runs_no_overlap"
T0 = datetime(2026, 11, 1, tzinfo=UTC)
SHA = "a" * 64

_EVENT = text(
    "INSERT INTO events (id, key, body, body_sha256, created_by, created_at, updated_at) "
    "VALUES (:id, :key, '{}'::jsonb, :sha, :by, now(), now())"
)
_RUN = text(
    "INSERT INTO event_runs (id, event_id, seq, status, starts_at, ends_at, snapshot, "
    "body_sha256, published_at, stopped_at, created_by, created_at) "
    "VALUES (:id, :event, :seq, :status, :s, :e, CAST(:snap AS jsonb), :sha, :pub, :stop, :by, "
    "now())"
)


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    from gv_testkit import apply_migrations

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    eng = create_async_engine(database_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def add_event(engine: AsyncEngine, key: str) -> Any:
    event = uuid7()
    async with engine.begin() as conn:
        await conn.execute(_EVENT, {"id": event, "key": key, "sha": SHA, "by": uuid7()})
    return event


async def add_run(
    engine: AsyncEngine,
    event: Any,
    start: datetime,
    end: datetime,
    *,
    status: str = "published",
    seq: int = 1,
    stopped_at: datetime | None = None,
) -> Any:
    run = uuid7()
    draft = status == "draft"
    async with engine.begin() as conn:
        await conn.execute(
            _RUN,
            {
                "id": run,
                "event": event,
                "seq": seq,
                "status": status,
                "s": start,
                "e": end,
                "snap": None if draft else "{}",
                "sha": None if draft else SHA,
                "pub": None if draft else T0,
                "stop": stopped_at,
                "by": uuid7(),
            },
        )
    return run


def day(n: int) -> datetime:
    return T0 + timedelta(days=n)


async def test_overlap_is_rejected_by_the_database_without_any_lock(engine: AsyncEngine) -> None:
    a, b = await add_event(engine, "one"), await add_event(engine, "two")
    await add_run(engine, a, day(0), day(10))

    with pytest.raises(IntegrityError, match=CONSTRAINT):
        await add_run(engine, b, day(5), day(15))  # другое событие: пересечение глобальное
    with pytest.raises(IntegrityError, match=CONSTRAINT):
        await add_run(engine, a, day(9), day(12), seq=2)  # то же событие


async def test_adjacent_periods_are_allowed_half_open(engine: AsyncEngine) -> None:
    a = await add_event(engine, "one")
    await add_run(engine, a, day(0), day(10))
    await add_run(engine, a, day(10), day(20), seq=2)  # [) : конец одного = начало другого
    await add_run(engine, a, day(-10), day(0), seq=3)


async def test_concurrent_inserts_let_only_one_through(engine: AsyncEngine) -> None:
    a = await add_event(engine, "one")
    results = await asyncio.gather(
        *(add_run(engine, a, day(0), day(10), seq=i) for i in range(1, 6)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, IntegrityError) for r in results) == 4


async def test_drafts_do_not_block_and_do_not_conflict(engine: AsyncEngine) -> None:
    a = await add_event(engine, "one")
    await add_run(engine, a, day(0), day(10), status="draft")
    await add_run(engine, a, day(0), day(10), status="draft", seq=2)
    await add_run(engine, a, day(5), day(8), seq=3)  # опубликованный поверх черновиков


async def test_stopped_run_frees_the_time_after_the_stop(engine: AsyncEngine) -> None:
    a = await add_event(engine, "one")
    await add_run(engine, a, day(0), day(10), status="stopped", stopped_at=day(4))

    with pytest.raises(IntegrityError, match=CONSTRAINT):  # до остановки время занято
        await add_run(engine, a, day(3), day(6), seq=2)
    await add_run(engine, a, day(4), day(8), seq=3)  # с момента остановки - свободно


async def test_stopped_before_start_is_an_empty_period(engine: AsyncEngine) -> None:
    """Будущий запуск, остановленный до старта, не занимает время вовсе (и не ломает диапазон)."""
    a = await add_event(engine, "one")
    await add_run(engine, a, day(5), day(10), status="stopped", stopped_at=day(1))
    await add_run(engine, a, day(5), day(10), seq=2)


async def test_downgrade_drops_the_constraint_and_upgrade_restores_it(
    engine: AsyncEngine, database_url: str
) -> None:
    assert DEFINITION.migrations_path is not None
    config = alembic_config(DEFINITION.migrations_path, database_url)
    a = await add_event(engine, "one")
    await add_run(engine, a, day(0), day(10))

    await asyncio.to_thread(command.downgrade, config, "0010")
    await add_run(engine, a, day(5), day(15), seq=2)  # без ограничения пересечение возможно

    async with engine.connect() as conn:
        left = (
            await conn.execute(
                text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": CONSTRAINT}
            )
        ).scalar_one()
    assert left == 0


async def test_upgrade_refuses_existing_overlaps_before_changing_anything(
    engine: AsyncEngine, database_url: str
) -> None:
    assert DEFINITION.migrations_path is not None
    config = alembic_config(DEFINITION.migrations_path, database_url)
    a, b = await add_event(engine, "one"), await add_event(engine, "two")
    await asyncio.to_thread(command.downgrade, config, "0010")
    first = await add_run(engine, a, day(0), day(10))
    second = await add_run(engine, b, day(5), day(15))

    with pytest.raises(RuntimeError) as failure:
        await asyncio.to_thread(command.upgrade, config, "head")

    message = str(failure.value)
    assert str(first) in message and str(second) in message
    assert "пересека" in message
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        constraint = (
            await conn.execute(
                text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": CONSTRAINT}
            )
        ).scalar_one()
    assert (version, constraint) == ("0010", 0)  # ничего не создано, версия не сдвинута

    async with engine.begin() as conn:  # админ развёл периоды - миграция проходит
        await conn.execute(
            text("UPDATE event_runs SET starts_at = :s, ends_at = :e WHERE id = :i"),
            {"s": day(10), "e": day(20), "i": second},
        )
    await asyncio.to_thread(command.upgrade, config, "head")
    with pytest.raises(IntegrityError, match=CONSTRAINT):
        await add_run(engine, a, day(11), day(12), seq=2)
