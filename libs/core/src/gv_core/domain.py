"""Базовые классы доменной модели: сущность, агрегат, доменное событие.

Правила:
    * агрегат меняется только через свои методы, которые проверяют инварианты;
    * изменения, интересные другим сервисам, агрегат фиксирует доменными событиями
      (``record``); Unit of Work после коммита кладёт их в outbox;
    * доменные события неизменяемы и содержат только данные (без ссылок на ORM и IO).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Факт, произошедший в домене.

    Наследник обязан задать ``aggregate_type`` (часть имени топика). Имя события по
    умолчанию — имя класса (``MatchFinished``), версия схемы — ``event_version``.

    Attributes:
        aggregate_id: Идентификатор агрегата; используется как ключ партиции, поэтому
            события одного агрегата обрабатываются строго по порядку.
    """

    aggregate_type: ClassVar[str]
    event_version: ClassVar[int] = 1

    aggregate_id: str

    @classmethod
    def event_name(cls) -> str:
        """Имя события без префикса сервиса, например ``MatchFinished``."""
        return cls.__name__

    def payload(self) -> dict[str, Any]:
        """Данные события для сериализации (всё, кроме ``aggregate_id``)."""
        return {
            f.name: _to_primitive(getattr(self, f.name))
            for f in dataclasses.fields(self)
            if f.name != "aggregate_id"
        }


def _to_primitive(value: Any) -> Any:  # noqa: PLR0911 — плоский диспетчер по типам
    """Привести значение к JSON-совместимому виду."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _to_primitive(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_primitive(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_to_primitive(v) for v in value]
    return value


class Entity[IdT]:
    """Сущность: равенство определяется идентификатором, а не значениями полей."""

    def __init__(self, id: IdT) -> None:  # noqa: A002 — поле домена называется id
        self.id = id

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self), self.id))


class AggregateRoot[IdT](Entity[IdT]):
    """Корень агрегата: граница консистентности и источник доменных событий.

    Attributes:
        version: Версия для оптимистичной блокировки. Репозиторий увеличивает её при
            сохранении и проверяет при обновлении.
    """

    def __init__(self, id: IdT, version: int = 0) -> None:  # noqa: A002
        super().__init__(id)
        self.version = version
        self._pending_events: list[DomainEvent] = []

    def record(self, event: DomainEvent) -> None:
        """Зафиксировать доменное событие для публикации после коммита."""
        self._pending_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Забрать накопленные события и очистить очередь (вызывает Unit of Work)."""
        events, self._pending_events = self._pending_events, []
        return events

    @property
    def pending_events(self) -> tuple[DomainEvent, ...]:
        """Накопленные, но ещё не забранные события (для тестов и отладки)."""
        return tuple(self._pending_events)
