"""Конверт доменного события — единый формат сообщений в Redpanda.

Формат зафиксирован в docs/architecture/communication.md §3.1. Изменение полей конверта —
ломающее изменение контракта всех сервисов, поэтому только через ADR.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from gv_core.clock import Clock
from gv_core.context import current_context
from gv_core.domain import DomainEvent
from gv_core.ids import uuid7

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class EventEnvelope(BaseModel):
    """Доменное событие с метаданными доставки.

    Attributes:
        event_id: Уникальный идентификатор (UUID v7) — ключ идемпотентности потребителей.
        event_type: ``<producer>.<EventName>``, например ``match.MatchFinished``.
        event_version: Версия схемы ``payload``.
        occurred_at: Когда событие произошло (UTC).
        producer: Сервис-источник.
        aggregate_type: Тип агрегата — часть имени топика.
        aggregate_id: Идентификатор агрегата — ключ партиции.
        correlation_id: Сквозной идентификатор цепочки.
        causation_id: Что непосредственно вызвало событие.
        payload: Данные события.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    event_id: UUID
    event_type: str = Field(min_length=3, max_length=200)
    event_version: int = Field(ge=1)
    occurred_at: datetime
    producer: str
    aggregate_type: str
    aggregate_id: str = Field(min_length=1, max_length=200)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    payload: dict[str, Any]

    @property
    def topic(self) -> str:
        """Топик события: ``<producer>.<aggregate_type>.v<event_version>``."""
        return topic_name(self.producer, self.aggregate_type, self.event_version)

    @classmethod
    def from_domain_event(cls, event: DomainEvent, *, producer: str, clock: Clock) -> EventEnvelope:
        """Упаковать доменное событие, взяв correlation/causation из текущего контекста."""
        ctx = current_context()
        return cls(
            event_id=uuid7(),
            event_type=f"{producer}.{event.event_name()}",
            event_version=event.event_version,
            occurred_at=clock.now(),
            producer=producer,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            correlation_id=ctx.correlation_id if ctx else None,
            causation_id=ctx.causation_id if ctx else None,
            payload=event.payload(),
        )

    def to_bytes(self) -> bytes:
        """Сериализовать в JSON."""
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> EventEnvelope:
        """Разобрать JSON. Бросает ``pydantic.ValidationError`` на битых данных."""
        return cls.model_validate_json(raw)


def topic_name(producer: str, aggregate_type: str, version: int) -> str:
    """Собрать имя топика и проверить формат частей."""
    for part in (producer, aggregate_type):
        if not _NAME_RE.match(part):
            raise ValueError(f"недопустимое имя части топика: {part!r}")
    return f"{producer}.{aggregate_type}.v{version}"


def dlq_topic(consumer_group: str) -> str:
    """Топик «мёртвых» сообщений для группы потребителей."""
    return f"{consumer_group}.dlq"
