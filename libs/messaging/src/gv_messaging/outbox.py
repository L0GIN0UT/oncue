"""Transactional outbox: запись событий в БД сервиса и их публикация в брокер.

Зачем: изменение данных и событие должны либо оба сохраниться, либо ни одно. Поэтому
событие пишется в таблицу ``outbox`` в той же транзакции, что и данные, а отдельный
процесс-relay публикует его в Redpanda (гарантия at-least-once). См. ADR 0004.

Порядок событий: relay в каждый момент времени один на сервис — лидер выбирается через
транзакционный advisory lock PostgreSQL. Строки читаются в порядке ``created_at, event_id``
и публикуются последовательно; на первой ошибке пачка прерывается. Так события одного
агрегата никогда не переставляются, даже если запущено несколько реплик relay.
"""

from __future__ import annotations

import asyncio
import contextlib
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core.clock import Clock
from gv_messaging.envelope import EventEnvelope
from gv_messaging.metrics import OUTBOX_LAG_SECONDS, OUTBOX_PUBLISH_ERRORS, OUTBOX_PUBLISHED
from gv_messaging.ports import EventPublisher

log = structlog.get_logger(__name__)

OUTBOX_TABLE = "outbox"
_MAX_ERROR_LEN = 2000


def outbox_table(metadata: MetaData) -> Table:
    """Объявить таблицу outbox в метаданных сервиса (для миграций и запросов)."""
    if OUTBOX_TABLE in metadata.tables:
        return metadata.tables[OUTBOX_TABLE]
    return Table(
        OUTBOX_TABLE,
        metadata,
        Column("event_id", Uuid(as_uuid=True), primary_key=True),
        Column("topic", String(255), nullable=False),
        Column("key", String(255), nullable=False),
        Column("event_type", String(255), nullable=False),
        Column("payload", LargeBinary, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("published_at", DateTime(timezone=True), nullable=True),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("last_error", Text, nullable=True),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # Частичный индекс: relay читает только неопубликованное, в порядке создания.
        Index(
            "ix_outbox_unpublished",
            "created_at",
            "event_id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )


class OutboxWriter:
    """Запись событий в outbox внутри текущей транзакции."""

    def __init__(self, table: Table) -> None:
        self._table = table

    async def add(self, session: AsyncSession, envelopes: list[EventEnvelope]) -> None:
        """Добавить события. Коммит выполняет вызывающий (Unit of Work)."""
        if not envelopes:
            return
        await session.execute(
            insert(self._table),
            [
                {
                    "event_id": e.event_id,
                    "topic": e.topic,
                    "key": e.aggregate_id,
                    "event_type": e.event_type,
                    "payload": e.to_bytes(),
                    "created_at": e.occurred_at,
                }
                for e in envelopes
            ],
        )


@dataclass(frozen=True, slots=True)
class RelaySettings:
    """Параметры relay.

    Attributes:
        lock_name: Имя сервиса; из него вычисляется ключ advisory lock лидера.
        batch_size: Сколько событий публиковать за одну транзакцию.
        idle_interval: Пауза, когда публиковать нечего или лидер — другой процесс.
        error_backoff: Пауза после ошибки публикации.
        retention: Сколько хранить опубликованные события перед удалением.
    """

    lock_name: str
    batch_size: int = 100
    idle_interval: float = 0.5
    error_backoff: float = 2.0
    retention: timedelta = timedelta(days=7)


class OutboxRelay:
    """Фоновый процесс публикации событий из outbox в брокер."""

    def __init__(
        self,
        table: Table,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        clock: Clock,
        settings: RelaySettings,
    ) -> None:
        self._table = table
        self._sessions = session_factory
        self._publisher = publisher
        self._clock = clock
        self._settings = settings
        # Ключ advisory lock — стабильный 32-битный хэш имени (crc32 детерминирован).
        self._lock_key = zlib.crc32(f"gv-outbox:{settings.lock_name}".encode())

    async def run_once(self) -> int:
        """Опубликовать одну пачку.

        Returns:
            Количество опубликованных событий. ``-1`` — лидер сейчас другой процесс.

        Raises:
            OutboxPublishError: брокер отклонил публикацию; попытка уже зафиксирована в БД.
        """
        t = self._table
        async with self._sessions() as session, session.begin():
            acquired = await session.scalar(select(func.pg_try_advisory_xact_lock(self._lock_key)))
            if not acquired:
                return -1

            rows = (
                await session.execute(
                    select(t.c.event_id, t.c.topic, t.c.key, t.c.event_type, t.c.payload)
                    .where(t.c.published_at.is_(None))
                    .order_by(t.c.created_at, t.c.event_id)
                    .limit(self._settings.batch_size)
                )
            ).all()

            published = 0
            failure: Exception | None = None
            for row in rows:
                try:
                    await self._publisher.publish(
                        row.topic, row.key, row.payload, {"event_type": row.event_type}
                    )
                except Exception as exc:
                    failure = exc
                    OUTBOX_PUBLISH_ERRORS.labels(row.topic).inc()
                    await session.execute(
                        update(t)
                        .where(t.c.event_id == row.event_id)
                        .values(attempts=t.c.attempts + 1, last_error=repr(exc)[:_MAX_ERROR_LEN])
                    )
                    log.warning(
                        "outbox.publish_failed",
                        event_id=str(row.event_id),
                        topic=row.topic,
                        error=repr(exc),
                    )
                    # Дальше не идём: следующее событие того же агрегата не должно обогнать это.
                    break
                await session.execute(
                    update(t)
                    .where(t.c.event_id == row.event_id)
                    .values(published_at=self._clock.now())
                )
                OUTBOX_PUBLISHED.labels(row.topic).inc()
                published += 1

            oldest: datetime | None = await session.scalar(
                select(func.min(t.c.created_at)).where(t.c.published_at.is_(None))
            )
            OUTBOX_LAG_SECONDS.set((self._clock.now() - oldest).total_seconds() if oldest else 0.0)
        # Транзакция закоммичена: опубликованное отмечено, счётчик попыток сохранён.
        if failure is not None:
            raise OutboxPublishError(published) from failure
        return published

    async def purge_published(self) -> int:
        """Удалить опубликованные события старше ``retention``."""
        t = self._table
        border = self._clock.now() - self._settings.retention
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                delete(t).where(t.c.published_at.is_not(None), t.c.published_at < border)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Крутить relay до сигнала остановки. Ошибки не роняют процесс."""
        log.info("outbox.relay_started", lock=self._settings.lock_name)
        while not stop.is_set():
            try:
                published = await self.run_once()
            except OutboxPublishError:
                await _wait(stop, self._settings.error_backoff)
                continue
            except Exception:
                # Например, БД недоступна. Логируем с трейсбеком и пробуем позже.
                log.exception("outbox.relay_iteration_failed")
                await _wait(stop, self._settings.error_backoff)
                continue
            if published <= 0:
                await _wait(stop, self._settings.idle_interval)
        log.info("outbox.relay_stopped")


class OutboxPublishError(Exception):
    """Публикация пачки прервана ошибкой брокера.

    Attributes:
        published: Сколько событий успели опубликовать до ошибки.
    """

    def __init__(self, published: int) -> None:
        super().__init__(f"публикация прервана после {published} событий")
        self.published = published


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    """Подождать ``seconds`` или проснуться раньше по сигналу остановки."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
