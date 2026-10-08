"""Unit of Work на SQLAlchemy с записью доменных событий в outbox."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import AggregateRoot
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter


class SqlAlchemyUnitOfWork:
    """Транзакция use-case: изменения агрегатов и их события коммитятся атомарно.

    Экземпляр одноразовый на одну операцию. Без явного ``commit()`` изменения откатываются
    при выходе из блока — «забытый коммит» не может тихо сохранить половину данных.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        outbox: OutboxWriter,
        *,
        producer: str,
        clock: Clock,
    ) -> None:
        self._sessions = session_factory
        self._outbox = outbox
        self._producer = producer
        self._clock = clock
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._tracked: list[AggregateRoot[Any]] = []
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        """Сессия текущей транзакции — для репозиториев."""
        if self._session is None:
            raise RuntimeError("Unit of Work не открыт: используйте `async with uow:`")
        return self._session

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work одноразовый и уже используется")
        self._session = self._sessions()
        self._tx = await self._session.begin()
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
            if self._session is not None:
                await self._session.close()

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отметить агрегат: его события попадут в outbox при коммите."""
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        """Записать события отслеживаемых агрегатов в outbox и закоммитить транзакцию."""
        if self._tx is None:
            raise RuntimeError("Unit of Work не открыт")
        envelopes = [
            EventEnvelope.from_domain_event(event, producer=self._producer, clock=self._clock)
            for aggregate in self._tracked
            for event in aggregate.pull_events()
        ]
        await self._outbox.add(self.session, envelopes)
        await self._tx.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Откатить транзакцию. События агрегатов при этом уже не нужны."""
        if self._tx is not None and self._tx.is_active:
            await self._tx.rollback()
        for aggregate in self._tracked:
            aggregate.pull_events()
