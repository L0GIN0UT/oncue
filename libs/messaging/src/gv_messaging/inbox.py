"""Inbox: дедупликация входящих событий.

Брокер гарантирует доставку «хотя бы один раз», значит дубли неизбежны. Потребитель в той же
транзакции, где меняет свои данные, вставляет ``(consumer, event_id)`` в ``inbox``. Если
строка уже есть — событие обработано раньше, повтор пропускается.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Uuid,
    delete,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

INBOX_TABLE = "inbox"


def inbox_table(metadata: MetaData) -> Table:
    """Объявить таблицу inbox в метаданных сервиса."""
    if INBOX_TABLE in metadata.tables:
        return metadata.tables[INBOX_TABLE]
    return Table(
        INBOX_TABLE,
        metadata,
        Column("consumer", String(255), nullable=False),
        Column("event_id", Uuid(as_uuid=True), nullable=False),
        Column("processed_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("consumer", "event_id"),
    )


class Inbox:
    """Отметки об обработанных событиях."""

    def __init__(self, table: Table) -> None:
        self._table = table

    async def try_mark_processed(
        self, session: AsyncSession, *, consumer: str, event_id: UUID, now: datetime
    ) -> bool:
        """Отметить событие обработанным внутри текущей транзакции.

        Returns:
            ``True`` — событие новое, его нужно обработать; ``False`` — дубль.
        """
        result = await session.execute(
            pg_insert(self._table)
            .values(consumer=consumer, event_id=event_id, processed_at=now)
            .on_conflict_do_nothing()
            .returning(self._table.c.event_id)
        )
        return result.first() is not None


async def purge_inbox(session: AsyncSession, table: Table, before: datetime) -> int:
    """Удалить отметки ``processed_at < before`` в текущей транзакции.

    Срок хранения должен превышать окно возможных повторов брокера (retention топика
    и ретраи), иначе старый дубль пройдёт как новое событие.

    Returns:
        Число удалённых строк.
    """
    result = await session.execute(delete(table).where(table.c.processed_at < before))
    return int(getattr(result, "rowcount", 0) or 0)
