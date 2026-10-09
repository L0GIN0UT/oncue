"""Постановка задач RabbitMQ из API и consumer: соединение открывается при первой задаче."""

from __future__ import annotations

import asyncio
from typing import Any

from gv_core.errors import DependencyUnavailableError
from gv_messaging.rabbit import RabbitTaskQueue


class LazyRabbitTaskQueue:
    """``TaskQueue`` поверх ``RabbitTaskQueue`` с ленивым подключением.

    Процессы api и consumer только ставят задачи; держать соединение с RabbitMQ с момента
    старта им не нужно, а экспорт OpenAPI и тесты собираются вовсе без брокера.
    """

    def __init__(self, url: str | None, service: str) -> None:
        self._url = url
        self._service = service
        self._queue: RabbitTaskQueue | None = None
        self._lock = asyncio.Lock()

    async def _get(self) -> RabbitTaskQueue:
        if self._url is None:
            raise DependencyUnavailableError("очередь задач не настроена (GV_RABBITMQ_URL)")
        async with self._lock:
            if self._queue is None:
                queue = RabbitTaskQueue(self._url, self._service)
                await queue.start()
                self._queue = queue
        return self._queue

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        """Поставить задачу (с подтверждением брокера)."""
        queue = await self._get()
        await queue.enqueue(task_type, payload, task_id=task_id)

    async def close(self) -> None:
        """Закрыть соединение, если открывалось."""
        if self._queue is not None:
            await self._queue.stop()
            self._queue = None
