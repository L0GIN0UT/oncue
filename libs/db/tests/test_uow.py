"""Unit of Work: данные и события коммитятся атомарно."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Column, Integer, Table, Uuid, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gv_core import AggregateRoot, DomainEvent, RequestContext, bind_context, uuid7
from gv_db import SqlAlchemyUnitOfWork, create_metadata, create_session_factory
from gv_db.metadata import ServiceTables
from gv_messaging import OutboxWriter
from gv_testkit import FakeClock, create_schema, outbox_envelopes

Env = tuple[ServiceTables, Table, async_sessionmaker[AsyncSession]]

pytestmark = pytest.mark.integration


@dataclass(frozen=True, kw_only=True)
class Bumped(DomainEvent):
    aggregate_type = "counter"
    value: int


class Counter(AggregateRoot[uuid.UUID]):
    def __init__(self, id: uuid.UUID) -> None:  # noqa: A002
        super().__init__(id)
        self.value = 0

    def bump(self) -> None:
        self.value += 1
        self.record(Bumped(aggregate_id=str(self.id), value=self.value))


@pytest.fixture
async def env(
    database_url: str,
) -> AsyncIterator[Env]:
    tables = create_metadata()
    counters = Table(
        "counters", tables.metadata, Column("id", Uuid, primary_key=True), Column("value", Integer)
    )
    await create_schema(database_url, tables.metadata)
    engine = create_async_engine(database_url)
    yield tables, counters, create_session_factory(engine)
    await engine.dispose()


def _uow(
    tables: ServiceTables, sessions: async_sessionmaker[AsyncSession], clock: FakeClock
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(
        sessions, OutboxWriter(tables.outbox), producer="counter", clock=clock
    )


async def test_commit_writes_data_and_events_atomically(env: Env, fake_clock: FakeClock) -> None:
    tables, counters, sessions = env
    counter = Counter(uuid7())
    correlation = uuid7()
    with bind_context(RequestContext(correlation_id=correlation)):
        async with _uow(tables, sessions, fake_clock) as uow:
            counter.bump()
            counter.bump()
            await uow.session.execute(insert(counters).values(id=counter.id, value=counter.value))
            uow.track(counter)
            await uow.commit()

    async with sessions() as s:
        assert await s.scalar(select(counters.c.value)) == 2
        events = await outbox_envelopes(s, tables.outbox)
    assert [e.payload["value"] for e in events] == [1, 2]
    assert {e.correlation_id for e in events} == {correlation}
    assert counter.pending_events == ()


async def test_without_commit_nothing_is_saved(env: Env, fake_clock: FakeClock) -> None:
    tables, counters, sessions = env
    counter = Counter(uuid7())
    async with _uow(tables, sessions, fake_clock) as uow:
        counter.bump()
        await uow.session.execute(insert(counters).values(id=counter.id, value=1))
        uow.track(counter)
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(counters)) == 0
        assert await s.scalar(select(func.count()).select_from(tables.outbox)) == 0
    assert counter.pending_events == ()


async def test_exception_rolls_back(env: Env, fake_clock: FakeClock) -> None:
    tables, counters, sessions = env
    with pytest.raises(RuntimeError):
        async with _uow(tables, sessions, fake_clock) as uow:
            await uow.session.execute(insert(counters).values(id=uuid7(), value=1))
            raise RuntimeError("бизнес-ошибка")
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(counters)) == 0


async def test_uow_is_single_use(env: Env, fake_clock: FakeClock) -> None:
    tables, _, sessions = env
    uow = _uow(tables, sessions, fake_clock)
    async with uow:
        pass
    with pytest.raises(RuntimeError, match="одноразовый"):
        async with uow:
            pass
