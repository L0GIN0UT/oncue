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
from catalog_service.domain.model import (
    ExternalSource,
    ItemData,
    MediaDomain,
    MediaKind,
    PersonCredit,
    PersonRole,
)
from catalog_service.domain.normalization import TitleNormalizer
from catalog_service.infrastructure.container import LazyTaskQueue
from catalog_service.infrastructure.search.opensearch import (
    OpenSearchIndex,
    added_properties,
    concrete_index,
    index_body,
)
from catalog_service.infrastructure.search.reindex import OpenSearchMigrator

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


def _track(title: str, artist: str, person: Any) -> SearchDocument:
    item = MediaItem.create(
        uuid7(),
        ItemData(
            kind=MediaKind.TRACK,
            title=title,
            year=2002,
            external_refs={ExternalSource.SPOTIFY: title},
            persons=(PersonCredit(person, artist, PersonRole.ARTIST, 0),),
        ),
        TitleNormalizer(),
        current_year=2026,
    )
    return SearchDocument.from_item(item)


async def test_opensearch_autocomplete_by_artist_and_scope(opensearch_url: str) -> None:
    alias = f"cattest-{uuid7().hex[-12:]}"
    index = OpenSearchIndex(opensearch_url, alias, timeout_s=10)
    eminem, missy = uuid7(), uuid7()
    try:
        yourself = _track("Lose Yourself", "Eminem", eminem)
        without = _track("Without Me", "Eminem", eminem)
        control = _track("Lose Control", "Missy Elliott", missy)
        for doc in (yourself, without, control):
            assert await index.index(doc)
        await index.refresh()

        by_artist = await index.autocomplete(SearchQuery("eminem", None, 10))
        assert {h.id for h in by_artist} == {yourself.id, without.id}
        assert {h.creator for h in by_artist} == {"Eminem"}
        # Автор и название вместе.
        both = await index.autocomplete(SearchQuery("eminem lose", None, 10))
        assert [h.id for h in both] == [yourself.id]
        # Область: все треки исполнителя, имя автора не ищется, автор не входит в поиск по другим.
        scoped = await index.autocomplete(SearchQuery("lo", None, 10, scope_person=eminem))
        assert [h.id for h in scoped] == [yourself.id]
        assert await index.autocomplete(SearchQuery("eminem", None, 10, scope_person=eminem)) == []
    finally:
        await index._client.indices.delete(index=concrete_index(alias), ignore_unavailable=True)
        await index.close()


async def test_reindex_upgrades_old_index_and_switches_alias(opensearch_url: str) -> None:
    """Старый v1: ensure_index добавляет поля, пересборка идёт в v2, откат возможен."""
    alias = f"cattest-{uuid7().hex[-12:]}"
    index = OpenSearchIndex(opensearch_url, alias, timeout_s=10)
    client = index._client
    eminem = uuid7()
    old_body = index_body()
    for name in added_properties():
        del old_body["mappings"]["properties"][name]
    try:
        await client.indices.create(index=f"{alias}_v1", body=old_body)
        await client.indices.put_alias(index=f"{alias}_v1", name=alias)
        await index.ensure_index()  # новый образ: поля добавлены в действующий индекс
        doc = _track("Lose Yourself", "Eminem", eminem)
        assert await index.index(doc)  # запись с новыми полями в v1 не падает (strict)
        await index.refresh()

        migrator = OpenSearchMigrator(client, alias)
        assert await migrator.live_indices() == (f"{alias}_v1",)
        await migrator.prepare()
        assert await migrator.write([doc]) == 1
        assert await migrator.write([doc]) == 0  # повтор той же версии — пропуск
        assert await migrator.count() == 1
        assert await migrator.switch() == (f"{alias}_v1",)
        assert await migrator.live_indices() == (f"{alias}_v2",)
        assert [h.id for h in await index.autocomplete(SearchQuery("eminem", None, 5))] == [doc.id]

        assert await migrator.rollback() == (f"{alias}_v2", f"{alias}_v1")
        assert await migrator.live_indices() == (f"{alias}_v1",)
    finally:
        await client.indices.delete(index=f"{alias}_v*", ignore_unavailable=True)
        await index.close()
