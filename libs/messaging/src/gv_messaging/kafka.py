"""Транспорт доменных событий через Redpanda (Kafka API).

Публикация: ``acks=all`` + идемпотентный продюсер — брокер не потеряет и не задвоит
подтверждённое сообщение. Потребление: ручной коммит offset строго после того, как судьба
сообщения решена (обработано, дубль, пропущено или отправлено в DLQ).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition

from gv_messaging.consumer import EventProcessor
from gv_messaging.envelope import dlq_topic

log = structlog.get_logger(__name__)


class KafkaEventPublisher:
    """Реализация ``EventPublisher`` поверх aiokafka."""

    def __init__(self, bootstrap_servers: str, client_id: str) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            client_id=client_id,
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
            request_timeout_ms=10_000,
        )

    async def start(self) -> None:
        """Подключиться к брокеру."""
        await self._producer.start()

    async def stop(self) -> None:
        """Дождаться отправки буфера и отключиться."""
        await self._producer.stop()

    async def publish(
        self, topic: str, key: str, value: bytes, headers: Mapping[str, str] | None = None
    ) -> None:
        """Опубликовать и дождаться подтверждения всех реплик."""
        await self._producer.send_and_wait(
            topic,
            value=value,
            key=key.encode(),
            headers=[(k, v.encode()) for k, v in (headers or {}).items()],
        )


class KafkaDeadLetterSink:
    """DLQ в отдельном топике ``<group>.dlq`` с причиной в заголовках."""

    def __init__(self, publisher: KafkaEventPublisher, group: str) -> None:
        self._publisher = publisher
        self._topic = dlq_topic(group)

    async def send(self, raw: bytes, *, reason: str, error: str, source: str) -> None:
        """Опубликовать сообщение в DLQ."""
        await self._publisher.publish(
            self._topic, key=source, value=raw, headers={"reason": reason, "error": error[:1000]}
        )


class KafkaEventConsumer:
    """Цикл чтения топиков группой потребителей."""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        group: str,
        topics: list[str],
        processor: EventProcessor,
        max_batch: int = 100,
    ) -> None:
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=bootstrap_servers,
            group_id=group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            isolation_level="read_committed",
        )
        self._processor = processor
        self._max_batch = max_batch
        self._group = group

    async def run(self, stop: asyncio.Event) -> None:
        """Читать и обрабатывать до сигнала остановки.

        Сообщения одной партиции обрабатываются последовательно — порядок событий агрегата
        сохраняется. Параллелизм — через число партиций и реплик потребителя.
        """
        await self._consumer.start()
        log.info("consumer.started", group=self._group)
        try:
            while not stop.is_set():
                batches = await self._consumer.getmany(timeout_ms=500, max_records=self._max_batch)
                for tp, messages in batches.items():
                    for message in messages:
                        await self._processor.process(
                            message.value or b"",
                            source=f"{tp.topic}:{tp.partition}:{message.offset}",
                        )
                        await self._consumer.commit(
                            {TopicPartition(tp.topic, tp.partition): message.offset + 1}
                        )
        finally:
            await self._consumer.stop()
            log.info("consumer.stopped", group=self._group)
