"""Обработка входящих доменных событий, не зависящая от транспорта.

``EventProcessor`` получает сырые байты сообщения и решает его судьбу:
разбор → поиск обработчика → дедупликация через inbox → обработка в транзакции →
ретраи → DLQ. Транспорт (``gv_messaging.kafka``) только доставляет байты и коммитит offset.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core.clock import Clock
from gv_core.context import RequestContext, bind_context
from gv_messaging.envelope import EventEnvelope
from gv_messaging.inbox import Inbox
from gv_messaging.metrics import EVENT_HANDLE_SECONDS, EVENTS_CONSUMED
from gv_messaging.ports import PermanentError
from gv_messaging.retry import RetryPolicy

log = structlog.get_logger(__name__)

EventHandler = Callable[[EventEnvelope, AsyncSession], Awaitable[None]]
"""Обработчик события. Работает внутри транзакции: всё, что он пишет через ``session``
(включая собственные события в outbox), коммитится атомарно вместе с отметкой в inbox."""


class Outcome(StrEnum):
    """Итог обработки сообщения."""

    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    SKIPPED = "skipped"  # тип события не интересен этой группе
    DEAD = "dead"  # отправлено в DLQ


class DeadLetterSink(Protocol):
    """Куда уходят сообщения, которые не удалось обработать."""

    async def send(self, raw: bytes, *, reason: str, error: str, source: str) -> None:
        """Сохранить сообщение с причиной. Должно быть надёжным: ошибка здесь — повод
        не коммитить offset и остановить потребителя."""
        ...


class EventProcessorPort(Protocol):
    """Обработчик сырых сообщений для транспорта (``KafkaEventConsumer``).

    Транспорт зависит от протокола, а не от ``EventProcessor``: сервис со своей обработкой
    (admin на Django) подставляет реализацию без ``cast``.
    """

    async def process(self, raw: bytes, *, source: str) -> Outcome:
        """Обработать сообщение; возврат означает, что offset можно коммитить."""
        ...


class EventHandlerRegistry:
    """Соответствие ``event_type`` → обработчик."""

    def __init__(self) -> None:
        self._handlers: dict[str, EventHandler] = {}

    def register(self, event_type: str, handler: EventHandler) -> None:
        """Зарегистрировать обработчик. Повторная регистрация — ошибка конфигурации."""
        if event_type in self._handlers:
            raise ValueError(f"обработчик для {event_type} уже зарегистрирован")
        self._handlers[event_type] = handler

    def on(self, event_type: str) -> Callable[[EventHandler], EventHandler]:
        """Декоратор для регистрации обработчика."""

        def decorator(handler: EventHandler) -> EventHandler:
            self.register(event_type, handler)
            return handler

        return decorator

    def get(self, event_type: str) -> EventHandler | None:
        """Обработчик или ``None``, если событие не интересно."""
        return self._handlers.get(event_type)

    @property
    def event_types(self) -> frozenset[str]:
        """Все обрабатываемые типы событий."""
        return frozenset(self._handlers)


class EventProcessor:
    """Обработка одного сообщения с гарантией «ровно один эффект»."""

    def __init__(
        self,
        *,
        group: str,
        registry: EventHandlerRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        inbox: Inbox,
        dead_letters: DeadLetterSink,
        clock: Clock,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._group = group
        self._registry = registry
        self._sessions = session_factory
        self._inbox = inbox
        self._dlq = dead_letters
        self._clock = clock
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    async def process(self, raw: bytes, *, source: str) -> Outcome:
        """Обработать сообщение. Возвращается, когда судьба сообщения решена окончательно,
        то есть offset можно коммитить."""
        try:
            envelope = EventEnvelope.from_bytes(raw)
        except ValidationError as exc:
            await self._dlq.send(raw, reason="malformed", error=str(exc)[:2000], source=source)
            EVENTS_CONSUMED.labels(self._group, "unknown", Outcome.DEAD).inc()
            log.error("event.malformed", source=source, group=self._group)  # noqa: TRY400
            return Outcome.DEAD

        handler = self._registry.get(envelope.event_type)
        if handler is None:
            return Outcome.SKIPPED

        attempt = 0
        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                outcome = await self._handle_once(envelope, handler)
            except PermanentError as exc:
                return await self._dead(raw, envelope, source, "permanent", exc)
            except Exception as exc:
                log.warning(
                    "event.handler_failed",
                    event_id=str(envelope.event_id),
                    event_type=envelope.event_type,
                    attempt=attempt,
                    exc_info=True,
                )
                if not self._retry.should_retry(attempt):
                    return await self._dead(raw, envelope, source, "retries_exhausted", exc)
                await self._sleep(self._retry.delay(attempt))
                continue
            finally:
                EVENT_HANDLE_SECONDS.labels(self._group, envelope.event_type).observe(
                    time.perf_counter() - started
                )
            EVENTS_CONSUMED.labels(self._group, envelope.event_type, outcome).inc()
            return outcome

    async def _handle_once(self, envelope: EventEnvelope, handler: EventHandler) -> Outcome:
        ctx = RequestContext(
            correlation_id=envelope.correlation_id or envelope.event_id,
            causation_id=envelope.event_id,
        )
        with bind_context(ctx):
            async with self._sessions() as session, session.begin():
                is_new = await self._inbox.try_mark_processed(
                    session,
                    consumer=self._group,
                    event_id=envelope.event_id,
                    now=self._clock.now(),
                )
                if not is_new:
                    return Outcome.DUPLICATE
                await handler(envelope, session)
        return Outcome.PROCESSED

    async def _dead(
        self, raw: bytes, envelope: EventEnvelope, source: str, reason: str, exc: Exception
    ) -> Outcome:
        await self._dlq.send(raw, reason=reason, error=repr(exc)[:2000], source=source)
        EVENTS_CONSUMED.labels(self._group, envelope.event_type, Outcome.DEAD).inc()
        log.error(
            "event.dead_lettered",
            event_id=str(envelope.event_id),
            event_type=envelope.event_type,
            reason=reason,
        )
        return Outcome.DEAD
