"""Очередь фоновых задач на RabbitMQ.

Топология для сервиса ``svc`` и типа задачи ``t``::

    svc.tasks        (direct) --t-->        svc.t              основная очередь (quorum)
    svc.tasks.retry  (direct) --t.N-->      svc.t.retry.N      TTL = delays[N], затем DLX
                                            обратно в svc.tasks с ключом t
    svc.tasks.dlq    (direct) --t-->        svc.t.dlq          «мёртвые» задачи

Ретраи — через delay-очереди с TTL, без ``sleep`` в обработчике: потребитель не блокируется,
задача возвращается позже. Исполнитель обязан быть идемпотентным по ``task_id``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import aio_pika
import structlog
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel, AbstractRobustConnection

from gv_messaging.metrics import TASKS_PROCESSED
from gv_messaging.ports import PermanentError, TaskHandler

log = structlog.get_logger(__name__)

DEFAULT_RETRY_DELAYS: tuple[float, ...] = (1.0, 10.0, 60.0, 600.0)


@dataclass(frozen=True, slots=True)
class RetryStep:
    """Отправить задачу в delay-очередь с индексом ``index``."""

    index: int


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """Отправить задачу в DLQ."""

    reason: str


def next_step(attempt: int, error: Exception, delays: tuple[float, ...]) -> RetryStep | DeadLetter:
    """Решить судьбу задачи после неудачной попытки номер ``attempt`` (с 1)."""
    if isinstance(error, PermanentError):
        return DeadLetter("permanent")
    if attempt > len(delays):
        return DeadLetter("retries_exhausted")
    return RetryStep(attempt - 1)


@dataclass(frozen=True, slots=True)
class TaskMessage:
    """Тело сообщения задачи."""

    task_id: str
    task_type: str
    payload: dict[str, Any]
    attempt: int = 1

    def to_bytes(self) -> bytes:
        """Сериализовать в JSON."""
        return json.dumps(
            {
                "task_id": self.task_id,
                "task_type": self.task_type,
                "payload": self.payload,
                "attempt": self.attempt,
            }
        ).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> TaskMessage:
        """Разобрать JSON. ``PermanentError`` на битых данных."""
        try:
            data = json.loads(raw)
            return cls(
                task_id=str(data["task_id"]),
                task_type=str(data["task_type"]),
                payload=dict(data["payload"]),
                attempt=int(data.get("attempt", 1)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise PermanentError("битое сообщение задачи") from exc


class RabbitTaskQueue:
    """Постановка и обработка задач одного сервиса."""

    def __init__(
        self, url: str, service: str, delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS
    ) -> None:
        self._url = url
        self._service = service
        self._delays = delays
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._declared: set[str] = set()

    async def start(self) -> None:
        """Подключиться (с автоматическим переподключением) и включить publisher confirms."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel(publisher_confirms=True)

    async def stop(self) -> None:
        """Закрыть соединение."""
        if self._connection is not None:
            await self._connection.close()

    @property
    def _ch(self) -> AbstractRobustChannel:
        if self._channel is None:
            raise RuntimeError("RabbitTaskQueue не запущена: вызовите start()")
        return self._channel

    def _names(self, task_type: str) -> dict[str, str]:
        s = self._service
        return {
            "exchange": f"{s}.tasks",
            "retry_exchange": f"{s}.tasks.retry",
            "dlq_exchange": f"{s}.tasks.dlq",
            "queue": f"{s}.{task_type}",
            "dlq": f"{s}.{task_type}.dlq",
        }

    async def declare(self, task_type: str) -> None:
        """Объявить топологию для типа задачи (идемпотентно)."""
        if task_type in self._declared:
            return
        n = self._names(task_type)
        ch = self._ch
        main_ex = await ch.declare_exchange(
            n["exchange"], aio_pika.ExchangeType.DIRECT, durable=True
        )
        retry_ex = await ch.declare_exchange(
            n["retry_exchange"], aio_pika.ExchangeType.DIRECT, durable=True
        )
        dlq_ex = await ch.declare_exchange(
            n["dlq_exchange"], aio_pika.ExchangeType.DIRECT, durable=True
        )

        queue = await ch.declare_queue(
            n["queue"], durable=True, arguments={"x-queue-type": "quorum"}
        )
        await queue.bind(main_ex, routing_key=task_type)

        for index, delay in enumerate(self._delays):
            retry_queue = await ch.declare_queue(
                f"{n['queue']}.retry.{index}",
                durable=True,
                arguments={
                    "x-message-ttl": int(delay * 1000),
                    "x-dead-letter-exchange": n["exchange"],
                    "x-dead-letter-routing-key": task_type,
                },
            )
            await retry_queue.bind(retry_ex, routing_key=f"{task_type}.{index}")

        dlq = await ch.declare_queue(n["dlq"], durable=True, arguments={"x-queue-type": "quorum"})
        await dlq.bind(dlq_ex, routing_key=task_type)
        self._declared.add(task_type)

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        """Поставить задачу (дожидается подтверждения брокера)."""
        await self.declare(task_type)
        await self._publish(
            self._names(task_type)["exchange"], task_type, TaskMessage(task_id, task_type, payload)
        )

    async def _publish(
        self, exchange: str, routing_key: str, message: TaskMessage, **headers: str
    ) -> None:
        ex = await self._ch.get_exchange(exchange)
        await ex.publish(
            aio_pika.Message(
                body=message.to_bytes(),
                message_id=message.task_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
                headers=dict(headers),
            ),
            routing_key=routing_key,
        )

    async def consume(
        self, task_type: str, handler: TaskHandler, stop: asyncio.Event, prefetch: int = 10
    ) -> None:
        """Обрабатывать задачи до сигнала остановки."""
        await self.declare(task_type)
        await self._ch.set_qos(prefetch_count=prefetch)
        queue = await self._ch.get_queue(self._names(task_type)["queue"])

        async def on_message(message: AbstractIncomingMessage) -> None:
            await self._handle(task_type, message, handler)

        tag = await queue.consume(on_message)
        log.info("tasks.consumer_started", queue=queue.name)
        try:
            await stop.wait()
        finally:
            await queue.cancel(tag)
            log.info("tasks.consumer_stopped", queue=queue.name)

    async def _handle(
        self, task_type: str, message: AbstractIncomingMessage, handler: TaskHandler
    ) -> None:
        names = self._names(task_type)
        try:
            task = TaskMessage.from_bytes(message.body)
        except PermanentError as exc:
            await self._dead(names, task_type, TaskMessage("unknown", task_type, {}), exc)
            await message.ack()
            return
        try:
            await handler(task.payload)
        except Exception as exc:
            step = next_step(task.attempt, exc, self._delays)
            if isinstance(step, RetryStep):
                log.warning("task.retry", task_id=task.task_id, attempt=task.attempt, exc_info=True)
                retried = TaskMessage(task.task_id, task.task_type, task.payload, task.attempt + 1)
                await self._publish(names["retry_exchange"], f"{task_type}.{step.index}", retried)
                TASKS_PROCESSED.labels(names["queue"], "retried").inc()
            else:
                await self._dead(names, task_type, task, exc, step.reason)
        else:
            TASKS_PROCESSED.labels(names["queue"], "done").inc()
        # ack только после того, как задача либо выполнена, либо надёжно переложена дальше.
        await message.ack()

    async def _dead(
        self,
        names: dict[str, str],
        task_type: str,
        task: TaskMessage,
        exc: Exception,
        reason: str = "permanent",
    ) -> None:
        await self._publish(
            names["dlq_exchange"], task_type, task, reason=reason, error=repr(exc)[:1000]
        )
        TASKS_PROCESSED.labels(names["queue"], "dead").inc()
        log.error("task.dead_lettered", task_id=task.task_id, reason=reason, exc_info=exc)
