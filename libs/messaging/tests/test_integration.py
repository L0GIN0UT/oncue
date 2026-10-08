"""Outbox relay и обработка событий на настоящем PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gv_core import DomainEvent
from gv_db import create_metadata, create_session_factory
from gv_db.metadata import ServiceTables
from gv_messaging import (
    EventEnvelope,
    EventHandlerRegistry,
    EventProcessor,
    Inbox,
    OutboxRelay,
    OutboxWriter,
    Outcome,
    PermanentError,
    RelaySettings,
    RetryPolicy,
)
from gv_messaging.outbox import OutboxPublishError
from gv_testkit import FakeClock, FakeDeadLetterSink, FakeEventPublisher, create_schema

Db = tuple[ServiceTables, async_sessionmaker[AsyncSession]]
Handler = Callable[[EventEnvelope, AsyncSession], Awaitable[None]]

pytestmark = pytest.mark.integration


@dataclass(frozen=True, kw_only=True)
class Scored(DomainEvent):
    aggregate_type = "game"
    points: int


@pytest.fixture
async def db(
    database_url: str,
) -> AsyncIterator[Db]:
    tables = create_metadata()
    await create_schema(database_url, tables.metadata)
    engine = create_async_engine(database_url)
    yield tables, create_session_factory(engine)
    await engine.dispose()


async def _write(
    sessions: async_sessionmaker[AsyncSession], tables: ServiceTables, clock: FakeClock, *keys: str
) -> list[EventEnvelope]:
    envelopes = []
    for i, key in enumerate(keys):
        clock.advance(seconds=1)
        envelopes.append(
            EventEnvelope.from_domain_event(
                Scored(aggregate_id=key, points=i), producer="game", clock=clock
            )
        )
    async with sessions() as session, session.begin():
        await OutboxWriter(tables.outbox).add(session, envelopes)
    return envelopes


def _relay(
    tables: ServiceTables,
    sessions: async_sessionmaker[AsyncSession],
    publisher: FakeEventPublisher,
    clock: FakeClock,
    name: str = "game",
) -> OutboxRelay:
    return OutboxRelay(
        tables.outbox, sessions, publisher, clock, RelaySettings(lock_name=name, batch_size=10)
    )


class TestOutboxRelay:
    async def test_publishes_in_order_and_marks_published(
        self, db: Db, fake_clock: FakeClock, fake_publisher: FakeEventPublisher
    ) -> None:
        tables, sessions = db
        written = await _write(sessions, tables, fake_clock, "a", "b", "a")

        assert await _relay(tables, sessions, fake_publisher, fake_clock).run_once() == 3
        assert [m.key for m in fake_publisher.messages] == ["a", "b", "a"]
        assert [EventEnvelope.from_bytes(m.value).event_id for m in fake_publisher.messages] == [
            e.event_id for e in written
        ]
        assert fake_publisher.messages[0].topic == "game.game.v1"
        assert fake_publisher.messages[0].headers == {"event_type": "game.Scored"}
        # Повторный запуск ничего не публикует повторно.
        assert await _relay(tables, sessions, fake_publisher, fake_clock).run_once() == 0
        assert len(fake_publisher.messages) == 3

    async def test_failure_stops_batch_keeps_order_and_records_attempt(
        self, db: Db, fake_clock: FakeClock
    ) -> None:
        tables, sessions = db
        await _write(sessions, tables, fake_clock, "a", "a", "a")
        publisher = FakeEventPublisher(fail_on={2})
        relay = _relay(tables, sessions, publisher, fake_clock)

        with pytest.raises(OutboxPublishError) as info:
            await relay.run_once()
        assert info.value.published == 1

        async with sessions() as s:
            rows = (
                await s.execute(
                    select(
                        tables.outbox.c.attempts,
                        tables.outbox.c.published_at,
                        tables.outbox.c.last_error,
                    ).order_by(tables.outbox.c.created_at)
                )
            ).all()
        assert rows[0].published_at is not None
        assert (
            rows[1].published_at is None
            and rows[1].attempts == 1
            and "ConnectionError" in rows[1].last_error
        )
        assert rows[2].published_at is None and rows[2].attempts == 0

        # Брокер ожил — оставшиеся уходят в исходном порядке.
        assert await relay.run_once() == 2
        assert len(publisher.messages) == 3

    async def test_only_one_relay_is_leader(
        self, db: Db, fake_clock: FakeClock, fake_publisher: FakeEventPublisher
    ) -> None:
        tables, sessions = db
        await _write(sessions, tables, fake_clock, "a")
        relay = _relay(tables, sessions, fake_publisher, fake_clock)

        # Держим advisory lock другим соединением — как будто работает вторая реплика.
        async with sessions() as holder, holder.begin():
            await holder.execute(select(func.pg_advisory_xact_lock(relay._lock_key)))
            assert await relay.run_once() == -1
        assert await relay.run_once() == 1

    async def test_purge_published(
        self, db: Db, fake_clock: FakeClock, fake_publisher: FakeEventPublisher
    ) -> None:
        tables, sessions = db
        await _write(sessions, tables, fake_clock, "a", "b")
        relay = _relay(tables, sessions, fake_publisher, fake_clock)
        await relay.run_once()
        assert await relay.purge_published() == 0
        fake_clock.advance(timedelta(days=8))
        assert await relay.purge_published() == 2

    async def test_run_forever_stops_on_signal(
        self, db: Db, fake_clock: FakeClock, fake_publisher: FakeEventPublisher
    ) -> None:
        tables, sessions = db
        await _write(sessions, tables, fake_clock, "a")
        stop = asyncio.Event()
        task = asyncio.create_task(
            _relay(tables, sessions, fake_publisher, fake_clock).run_forever(stop)
        )
        for _ in range(50):
            if fake_publisher.messages:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=3)
        assert len(fake_publisher.messages) == 1


class TestEventProcessor:
    async def _processor(
        self,
        db: Db,
        fake_clock: FakeClock,
        handler: Handler,
        retry: RetryPolicy | None = None,
    ) -> tuple[EventProcessor, FakeDeadLetterSink]:
        tables, sessions = db
        registry = EventHandlerRegistry()
        registry.register("game.Scored", handler)
        dlq = FakeDeadLetterSink()

        async def no_sleep(_: float) -> None:
            return None

        processor = EventProcessor(
            group="rating.consumer",
            registry=registry,
            session_factory=sessions,
            inbox=Inbox(tables.inbox),
            dead_letters=dlq,
            clock=fake_clock,
            retry=retry or RetryPolicy(max_attempts=3),
            sleep=no_sleep,
        )
        return processor, dlq

    def _raw(self, fake_clock: FakeClock, event_type_suffix: str = "") -> bytes:
        env = EventEnvelope.from_domain_event(
            Scored(aggregate_id="g", points=5), producer="game", clock=fake_clock
        )
        return env.to_bytes()

    async def test_processes_once_and_dedups(self, db: Db, fake_clock: FakeClock) -> None:
        seen: list[int] = []

        async def handler(env: EventEnvelope, session: AsyncSession) -> None:
            seen.append(env.payload["points"])

        processor, dlq = await self._processor(db, fake_clock, handler)
        raw = self._raw(fake_clock)
        assert await processor.process(raw, source="t:0:1") is Outcome.PROCESSED
        assert await processor.process(raw, source="t:0:1") is Outcome.DUPLICATE
        assert seen == [5]
        assert dlq.letters == []

    async def test_transient_failure_retries_without_double_effect(
        self, db: Db, fake_clock: FakeClock
    ) -> None:
        tables, _ = db
        calls = 0

        async def flaky(env: EventEnvelope, session: AsyncSession) -> None:
            nonlocal calls
            calls += 1
            # Пишем в БД и падаем: транзакция (вместе с отметкой inbox) должна откатиться.
            await session.execute(
                tables.inbox.insert().values(
                    consumer="side", event_id=env.event_id, processed_at=fake_clock.now()
                )
            )
            if calls < 3:
                raise ConnectionError("временно")

        processor, dlq = await self._processor(db, fake_clock, flaky)
        assert await processor.process(self._raw(fake_clock), source="s") is Outcome.PROCESSED
        assert calls == 3
        assert dlq.letters == []
        async with db[1]() as s:
            assert (
                await s.scalar(
                    select(func.count())
                    .select_from(tables.inbox)
                    .where(tables.inbox.c.consumer == "side")
                )
                == 1
            )

    async def test_exhausted_retries_go_to_dlq(self, db: Db, fake_clock: FakeClock) -> None:
        async def always_fails(env: EventEnvelope, session: AsyncSession) -> None:
            raise RuntimeError("всегда")

        processor, dlq = await self._processor(
            db, fake_clock, always_fails, RetryPolicy(max_attempts=2)
        )
        assert await processor.process(self._raw(fake_clock), source="s") is Outcome.DEAD
        assert dlq.letters[0]["reason"] == "retries_exhausted"

    async def test_permanent_error_skips_retries(self, db: Db, fake_clock: FakeClock) -> None:
        calls = 0

        async def bad(env: EventEnvelope, session: AsyncSession) -> None:
            nonlocal calls
            calls += 1
            raise PermanentError("контракт нарушен")

        processor, dlq = await self._processor(db, fake_clock, bad)
        assert await processor.process(self._raw(fake_clock), source="s") is Outcome.DEAD
        assert calls == 1
        assert dlq.letters[0]["reason"] == "permanent"

    async def test_malformed_and_unknown(self, db: Db, fake_clock: FakeClock) -> None:
        async def handler(env: EventEnvelope, session: AsyncSession) -> None:
            raise AssertionError("не должен вызываться")

        processor, dlq = await self._processor(db, fake_clock, handler)
        assert await processor.process(b"garbage", source="s") is Outcome.DEAD
        assert dlq.letters[0]["reason"] == "malformed"

        other = EventEnvelope.from_domain_event(
            Scored(aggregate_id="g", points=1), producer="match", clock=fake_clock
        )
        assert await processor.process(other.to_bytes(), source="s") is Outcome.SKIPPED
