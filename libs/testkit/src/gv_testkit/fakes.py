"""Фейки портов для unit-тестов: детерминированные и без IO."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from gv_core.domain import AggregateRoot, DomainEvent
from gv_core.errors import ConflictError
from gv_db.idempotency import IdempotencyRecord


class FakeClock:
    """Часы, которые идут только по команде."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("FakeClock требует timezone-aware datetime")

    def now(self) -> datetime:
        """Текущее «время»."""
        return self._now

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> datetime:
        """Сдвинуть время вперёд: ``advance(seconds=5)``."""
        self._now += delta or timedelta(**kwargs)
        return self._now


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    """Сообщение, отправленное через ``FakeEventPublisher``."""

    topic: str
    key: str
    value: bytes
    headers: Mapping[str, str]


@dataclass
class FakeEventPublisher:
    """Запоминает публикации; умеет имитировать отказ брокера.

    Attributes:
        fail_on: Номера вызовов (с 1), на которых бросить исключение.
    """

    messages: list[PublishedMessage] = field(default_factory=list)
    fail_on: set[int] = field(default_factory=set)
    calls: int = 0

    async def publish(
        self, topic: str, key: str, value: bytes, headers: Mapping[str, str] | None = None
    ) -> None:
        """Записать сообщение или бросить ``ConnectionError`` на заданном вызове."""
        self.calls += 1
        if self.calls in self.fail_on:
            raise ConnectionError("брокер недоступен (фейк)")
        self.messages.append(PublishedMessage(topic, key, value, dict(headers or {})))


@dataclass
class FakeDeadLetterSink:
    """DLQ в памяти."""

    letters: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, raw: bytes, *, reason: str, error: str, source: str) -> None:
        """Сохранить «мёртвое» сообщение."""
        self.letters.append({"raw": raw, "reason": reason, "error": error, "source": source})


@dataclass
class InMemoryTaskQueue:
    """Очередь задач в памяти: проверяем, что use-case поставил нужную задачу."""

    tasks: list[dict[str, Any]] = field(default_factory=list)

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        """Запомнить задачу."""
        self.tasks.append({"task_type": task_type, "payload": payload, "task_id": task_id})


class FakeUnitOfWork:
    """Unit of Work в памяти: собирает события вместо записи в outbox.

    Attributes:
        committed: Был ли вызван ``commit``.
        events: Доменные события, «сохранённые» при коммитах.
    """

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.events: list[DomainEvent] = []
        self._tracked: list[AggregateRoot[Any]] = []

    async def __aenter__(self) -> Self:
        self.committed = False
        self.rolled_back = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self.committed:
            await self.rollback()

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отслеживать агрегат."""
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        """«Сохранить» события отслеживаемых агрегатов."""
        for aggregate in self._tracked:
            self.events.extend(aggregate.pull_events())
        self.committed = True

    async def rollback(self) -> None:
        """Отбросить несохранённые события."""
        for aggregate in self._tracked:
            aggregate.pull_events()
        self.rolled_back = True


class InMemoryIdempotencyStore:
    """``gv_db.idempotency.IdempotencyStore`` в памяти — для unit-тестов use-case.

    Повторный ``save`` того же ``(actor_id, key)`` даёт ``idempotency.in_progress``,
    как UNIQUE в PostgreSQL. Транзакций нет: откат мутации фейк не моделирует.
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self._clock = clock or FakeClock()
        self.records: dict[tuple[UUID, str], IdempotencyRecord] = {}

    async def find(self, actor_id: UUID, key: str) -> IdempotencyRecord | None:
        """Сохранённый результат или ``None``."""
        return self.records.get((actor_id, key))

    async def save(
        self,
        actor_id: UUID,
        key: str,
        request_hash: str,
        response: dict[str, Any],
        *,
        status_code: int = 200,
    ) -> None:
        """Сохранить результат; занятый ключ — ``ConflictError(idempotency.in_progress)``."""
        if (actor_id, key) in self.records:
            raise ConflictError(
                "запрос с этим Idempotency-Key уже выполняется", code="idempotency.in_progress"
            )
        self.records[actor_id, key] = IdempotencyRecord(
            actor_id=actor_id,
            key=key,
            request_hash=request_hash,
            response=response,
            status_code=status_code,
            created_at=self._clock.now(),
        )
