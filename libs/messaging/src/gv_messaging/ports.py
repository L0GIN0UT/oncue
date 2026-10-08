"""Порты обмена сообщениями. Реализации: ``kafka``, ``rabbit``; фейки — в gv_testkit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol


class EventPublisher(Protocol):
    """Публикация байтов в топик лога событий."""

    async def publish(
        self, topic: str, key: str, value: bytes, headers: Mapping[str, str] | None = None
    ) -> None:
        """Опубликовать сообщение. Возвращается только после подтверждения брокером."""
        ...


TaskHandler = Callable[[dict[str, Any]], Awaitable[None]]


class TaskQueue(Protocol):
    """Очередь фоновых задач с ретраями и DLQ."""

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        """Поставить задачу. ``task_id`` — ключ идемпотентности исполнителя."""
        ...


class PermanentError(Exception):
    """Ошибка, повтор которой бессмыслен (битые данные, нарушенный контракт).

    Обработчик события или задачи бросает её, чтобы сообщение сразу ушло в DLQ без ретраев.
    """
