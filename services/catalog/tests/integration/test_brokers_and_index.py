"""RabbitMQ-задача импорта и индекс OpenSearch на реальной инфраструктуре.

OpenSearch локально может отсутствовать — тесты с фикстурой ``opensearch_url`` тогда
пропускаются (в CI — testcontainers/service-контейнер).
"""

from __future__ import annotations

import asyncio
from typing import Any

import aio_pika
import pytest
from catalog_service.application.commands.imports import IMPORT_TASK
from catalog_service.application.dto import SearchDocument, SearchQuery
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ExternalSource, ItemData, MediaDomain, MediaKind
from catalog_service.domain.normalization import TitleNormalizer
from catalog_service.infrastructure.container import LazyTaskQueue
from catalog_service.infrastructure.search.opensearch import OpenSearchIndex, concrete_index

from gv_core.ids import uuid7
from gv_messaging.rabbit import RabbitTaskQueue
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration


async def test_import_task_roundtrip_through_rabbitmq(infra: InfraProvider) -> None:
    url = infra.rabbitmq_url()
    service = f"cattest{uuid7().hex[-12:]}"
    producer = LazyTaskQueue(url, service)
    consumer = RabbitTaskQueue(url, service)
    received: list[dict[str, Any]] = []
    stop = asyncio.Event()

    async def handler(payload: dict[str, Any]) -> None:
        received.append(payload)
        stop.set()

    await consumer.start()
    try:
        task = asyncio.create_task(consumer.consume(IMPORT_TASK, handler, stop))
        await producer.enqueue(IMPORT_TASK, {"import_id": "x"}, task_id="x")
        async with asyncio.timeout(10):
            await task
        assert received == [{"import_id": "x"}]
    finally:
        await producer.close()
        await consumer.stop()
        await _cleanup(url, service)


async def _cleanup(url: str, service: str) -> None:
    """Удалить только свои очереди и обменники (общий брокер с другими потоками)."""
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        queue = f"{service}.{IMPORT_TASK}"
        for name in [queue, f"{queue}.dlq", *(f"{queue}.retry.{i}" for i in range(4))]:
            await channel.queue_delete(name)
        for name in (f"{service}.tasks", f"{service}.tasks.retry", f"{service}.tasks.dlq"):
            await channel.exchange_delete(name)
    finally:
        await connection.close()


def _doc(title: str, *, version: int = 1, blocked: bool = False) -> SearchDocument:
    item = MediaItem.create(
        uuid7(),
        ItemData(kind=MediaKind.FILM, title=title, external_refs={ExternalSource.TMDB: title}),
        TitleNormalizer(),
        current_year=2026,
    )
    if blocked:
        item.block("moderation", None)
    doc = SearchDocument.from_item(item)
    return SearchDocument(
        **{
            **{f: getattr(doc, f) for f in doc.__dataclass_fields__},
            "version": version + item.version - 1,
        }
    )


async def test_opensearch_index_versions_and_autocomplete(opensearch_url: str) -> None:
    alias = f"cattest-{uuid7().hex[-12:]}"
    index = OpenSearchIndex(opensearch_url, alias, timeout_s=10)
    try:
        film = _doc("Интерстеллар", version=2)
        hidden = _doc("Интернационал", blocked=True)
        assert await index.index(film)
        assert not await index.index(film)  # повтор той же версии
        older = SearchDocument(
            **{**{f: getattr(film, f) for f in film.__dataclass_fields__}, "version": 1}
        )
        assert not await index.index(older)  # устаревшая версия отброшена
        assert await index.index(hidden)
        await index.refresh()

        hits = await index.autocomplete(SearchQuery("инте", None, 10))
        assert [h.id for h in hits] == [film.id]  # заблокированная не в выдаче
        assert await index.autocomplete(SearchQuery("инте", MediaDomain.MUSIC, 10)) == []
        assert [h.id for h in await index.autocomplete(SearchQuery("interst", None, 5))] == [
            film.id
        ]
        assert film.id in {h.id for h in await index.search(SearchQuery("интерстелар", None, 5))}
    finally:
        await index._client.indices.delete(index=concrete_index(alias), ignore_unavailable=True)
        await index.close()
