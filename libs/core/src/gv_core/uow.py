"""Порт Unit of Work.

Use-case работает с транзакцией через этот интерфейс и не знает, что внутри SQLAlchemy.
Реализация для PostgreSQL — ``gv_db.uow.SqlAlchemyUnitOfWork``, фейк для unit-тестов —
``gv_testkit.fakes.FakeUnitOfWork``.

Пример::

    async with uow:
        game = await games.get(game_id)
        game.submit_guess(answer, clock.now())
        uow.track(game)
        await uow.commit()   # изменения + события в outbox — одной транзакцией
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol, Self

from gv_core.domain import AggregateRoot


class UnitOfWork(Protocol):
    """Граница транзакции use-case."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Откатить транзакцию, если не было ``commit`` или блок завершился исключением."""
        ...

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отметить агрегат, чьи доменные события нужно сохранить при коммите."""
        ...

    async def commit(self) -> None:
        """Сохранить изменения и события атомарно."""
        ...

    async def rollback(self) -> None:
        """Отменить изменения."""
        ...
