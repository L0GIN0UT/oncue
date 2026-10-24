"""Unit of Work друзей и конфига: репозитории, ключи идемпотентности, outbox (поток F1).

Работает в двух режимах: своя транзакция (API) или SAVEPOINT в транзакции consumer
(``session=``): тогда обработчик события и отметка inbox коммитятся атомарно.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import DomainEvent
from gv_db.idempotency import SqlIdempotencyStore
from gv_messaging import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from social_service.infrastructure.db.repositories_config import SqlConfigStore
from social_service.infrastructure.db.repositories_friends import SqlFriendsRepository
from social_service.infrastructure.db.tables import TABLES
from social_service.infrastructure.db.tables_friends import idempotency_keys


class SqlFriendsUnitOfWork:
    """Транзакция use-case друзей и конфига."""

    repo: SqlFriendsRepository
    configs: SqlConfigStore
    idempotency: SqlIdempotencyStore

    def __init__(
        self,
        *,
        producer: str,
        clock: Clock,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if (sessions is None) == (session is None):
            raise ValueError("нужен ровно один из sessions или session")
        self._sessions = sessions
        self._external = session
        self._outbox = OutboxWriter(TABLES.outbox)
        self._producer = producer
        self._clock = clock
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._events: list[DomainEvent] = []
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work одноразовый")
        if self._external is not None:
            self._session = self._external
            self._tx = await self._session.begin_nested()
        else:
            assert self._sessions is not None  # noqa: S101 - проверено в __init__
            self._session = self._sessions()
            self._tx = await self._session.begin()
        self.repo = SqlFriendsRepository(self._session)
        self.configs = SqlConfigStore(self._session)
        self.idempotency = SqlIdempotencyStore(self._session, idempotency_keys, self._clock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            if self._external is None and self._session is not None:
                await self._session.close()

    def publish(self, event: DomainEvent) -> None:
        """Событие уйдёт в outbox при коммите."""
        self._events.append(event)

    async def commit(self) -> None:
        """События - в outbox, затем коммит (или RELEASE SAVEPOINT)."""
        if self._tx is None or self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        envelopes = [
            EventEnvelope.from_domain_event(event, producer=self._producer, clock=self._clock)
            for event in self._events
        ]
        await self._outbox.add(self._session, envelopes)
        self._events = []
        await self._tx.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Откатить (транзакцию или SAVEPOINT)."""
        if self._tx is not None and self._tx.is_active:
            await self._tx.rollback()
        self._events = []
