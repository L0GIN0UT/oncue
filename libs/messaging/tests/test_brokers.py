"""Интеграционные тесты адаптеров брокеров: Redpanda (события) и RabbitMQ (задачи).

Брокер берётся из ``GV_TEST_KAFKA_BOOTSTRAP`` / ``GV_TEST_RABBITMQ_URL`` или поднимается
testcontainers; локально без брокера тесты пропускаются, в CI — падают.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aio_pika
import pytest

from gv_core import uuid7
from gv_messaging import PermanentError
from gv_messaging.kafka import KafkaEventConsumer, KafkaEventPublisher
from gv_messaging.rabbit import RabbitTaskQueue
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration


async def _wait_until(predicate: Any, limit_s: float = 20.0) -> None:
    async with asyncio.timeout(limit_s):
        while not predicate():  # noqa: ASYNC110 — опрос состояния внешнего брокера, события нет
            await asyncio.sleep(0.05)


class _RecordingProcessor:
    """Подменяет EventProcessor: адаптер отвечает только за доставку и offset."""

    def __init__(self) -> None:
        self.received: list[tuple[bytes, str]] = []

    async def process(self, raw: bytes, *, source: str) -> None:
        self.received.append((raw, source))


class TestKafka:
    async def test_ordered_delivery_and_committed_offsets(self, infra: InfraProvider) -> None:
        bootstrap = infra.kafka_bootstrap()
        topic, group = f"test.{uuid7().hex}.v1", f"test-{uuid7().hex}"
        publisher = KafkaEventPublisher(bootstrap, client_id="test")
        await publisher.start()
        try:
            for i in range(3):
                await publisher.publish(topic, key="agg-1", value=f"m{i}".encode())
        finally:
            await publisher.stop()

        async def consume_once() -> list[bytes]:
            processor = _RecordingProcessor()
            consumer = KafkaEventConsumer(
                bootstrap_servers=bootstrap,
                group=group,
                topics=[topic],
                processor=processor,  # type: ignore[arg-type]
            )
            stop = asyncio.Event()
            task = asyncio.create_task(consumer.run(stop))
            try:
                await asyncio.sleep(5)  # ребаланс группы + чтение
            finally:
                stop.set()
                await task
            return [raw for raw, _ in processor.received]

        assert await consume_once() == [b"m0", b"m1", b"m2"]  # порядок одного ключа сохранён
        assert await consume_once() == []  # offset закоммичен — повторной доставки нет


class TestRabbit:
    @staticmethod
    async def _queue(infra: InfraProvider) -> RabbitTaskQueue:
        # Хвост uuid7 случайный (начало — время): у каждого теста своя топология очередей.
        service = f"test{uuid7().hex[-12:]}"
        queue = RabbitTaskQueue(infra.rabbitmq_url(), service=service, delays=(0.1, 0.2))
        await queue.start()
        return queue

    @staticmethod
    async def _dlq_size(url: str, name: str) -> int:
        connection = await aio_pika.connect(url)
        async with connection:
            channel = await connection.channel()
            declared = await channel.declare_queue(name, passive=True)
            return declared.declaration_result.message_count or 0

    @classmethod
    async def _wait_dlq(cls, url: str, name: str, limit_s: float = 10.0) -> int:
        """Счётчик сообщений quorum-очереди обновляется с задержкой — ждём, а не спим."""
        deadline = asyncio.get_running_loop().time() + limit_s
        while (size := await cls._dlq_size(url, name)) == 0:
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)  # убедиться, что лишних сообщений не прибавится
        return max(size, await cls._dlq_size(url, name))

    async def test_task_is_delivered(self, infra: InfraProvider) -> None:
        queue = await self._queue(infra)
        seen: list[dict[str, Any]] = []

        async def handler(payload: dict[str, Any]) -> None:
            seen.append(payload)

        stop = asyncio.Event()
        try:
            await queue.enqueue("cut_audio", {"item": 1}, task_id="t1")
            consumer = asyncio.create_task(queue.consume("cut_audio", handler, stop))
            await _wait_until(lambda: seen == [{"item": 1}])
        finally:
            stop.set()
            await consumer
            await queue.stop()

    async def test_retries_then_dead_letter(self, infra: InfraProvider) -> None:
        queue = await self._queue(infra)
        attempts = 0

        async def always_fails(payload: dict[str, Any]) -> None:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("временный сбой")

        stop = asyncio.Event()
        try:
            await queue.enqueue("cut_audio", {"item": 2}, task_id="t2")
            consumer = asyncio.create_task(queue.consume("cut_audio", always_fails, stop))
            # 1 исходная попытка + 2 ретрая по числу задержек, затем DLQ
            await _wait_until(lambda: attempts == 3)
            dlq = queue._names("cut_audio")["dlq"]
            assert await self._wait_dlq(infra.rabbitmq_url(), dlq) == 1
            assert attempts == 3  # после DLQ попыток больше нет
        finally:
            stop.set()
            await consumer
            await queue.stop()

    async def test_permanent_error_skips_retries(self, infra: InfraProvider) -> None:
        queue = await self._queue(infra)
        attempts = 0

        async def rejects(payload: dict[str, Any]) -> None:
            nonlocal attempts
            attempts += 1
            raise PermanentError("невалидная задача")

        stop = asyncio.Event()
        try:
            await queue.enqueue("cut_audio", {"item": 3}, task_id="t3")
            consumer = asyncio.create_task(queue.consume("cut_audio", rejects, stop))
            dlq = queue._names("cut_audio")["dlq"]
            assert await self._wait_dlq(infra.rabbitmq_url(), dlq) == 1
            assert attempts == 1
        finally:
            stop.set()
            await consumer
            await queue.stop()
