"""Репозитории, outbox, trigram-fallback и потребление событий на реальном Postgres/Redis."""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from catalog_fakes import FakeSearch
from catalog_service.application.commands.admin import AdminContext
from catalog_service.application.commands.imports import UpsertImportedItem
from catalog_service.application.dto import (
    AdminItemFilter,
    ClueStatus,
    ImportedItem,
    PersonRef,
    RecordedAction,
    SearchQuery,
)
from catalog_service.domain.import_job import ImportJob, ImportSource, ItemOutcome
from catalog_service.domain.model import (
    AliasDraft,
    AliasKind,
    AliasSource,
    ExternalSource,
    MediaDomain,
    MediaKind,
    MediaMetrics,
    PersonRole,
    SourceMediaDraft,
    SourceMediaKind,
    SourceMediaOrigin,
)
from catalog_service.infrastructure.container import CatalogContainer
from catalog_service.infrastructure.messaging import build_event_handlers
from catalog_service.infrastructure.search.redis_cache import RedisPrefixCache, RedisRateLimiter
from catalog_service.infrastructure.search.trgm import TrgmSearch
from sqlalchemy import select

from gv_core.errors import ConflictError
from gv_core.ids import uuid7
from gv_messaging import EventProcessor, Inbox, Outcome
from gv_service import Infrastructure
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    assert_event_matches_schema,
    load_example,
    outbox_envelopes,
)

pytestmark = pytest.mark.integration


def film(tmdb: str = "157336", title: str = "Интерстеллар", **kw: object) -> ImportedItem:
    base: dict[str, object] = {
        "kind": MediaKind.FILM,
        "title": title,
        "original_title": "Interstellar" if title == "Интерстеллар" else None,
        "year": 2014,
        "persons": (PersonRef("tmdb:525", "Кристофер Нолан", PersonRole.DIRECTOR),),
        "genres": ("фантастика",),
        "external_refs": {ExternalSource.TMDB: tmdb},
        "metrics": MediaMetrics(popularity=100.0, vote_count=10),
        "source_media": (
            SourceMediaDraft(SourceMediaKind.POSTER, SourceMediaOrigin.TMDB, "https://i/p.jpg"),
        ),
    }
    base.update(kw)
    return ImportedItem(**base)  # type: ignore[arg-type]


async def upsert(c: CatalogContainer, item: ImportedItem) -> ItemOutcome:
    return await UpsertImportedItem(c.uow, c.normalizer, c.infra.clock)(item)


async def item_id_by_tmdb(c: CatalogContainer, tmdb: str) -> uuid.UUID:
    async with c.uow() as uow:
        item = await uow.items.find_by_external_ref(ExternalSource.TMDB, tmdb)
    assert item is not None
    return item.id


# ------------------------------------------------------------------ репозитории


async def test_item_roundtrip_and_outbox_events_match_schema(container: CatalogContainer) -> None:
    assert await upsert(container, film()) is ItemOutcome.CREATED
    assert await upsert(container, film()) is ItemOutcome.UNCHANGED
    assert await upsert(container, film(year=2015)) is ItemOutcome.UPDATED
    item_id = await item_id_by_tmdb(container, "157336")

    async with container.uow() as uow:
        item = await uow.items.get(item_id)
        envelopes = await outbox_envelopes(uow.session, container.infra.tables.outbox)  # type: ignore[union-attr]
    assert item is not None
    assert item.version == 2
    assert item.persons[0].name == "Кристофер Нолан"
    assert item.genres == ("фантастика",)
    assert item.metrics.vote_count == 10
    assert item.source_media[0].kind is SourceMediaKind.POSTER
    assert [e.event_type for e in envelopes] == [
        "catalog.MediaItemCreated",
        "catalog.MediaItemUpdated",
    ]
    for envelope in envelopes:
        assert envelope.topic == "catalog.media_item.v1"
        assert envelope.aggregate_id == str(item_id)
        assert_event_matches_schema(envelope)


async def test_optimistic_lock(container: CatalogContainer) -> None:
    await upsert(container, film())
    item_id = await item_id_by_tmdb(container, "157336")
    async with container.uow() as uow1:
        stale = await uow1.items.get(item_id)
    await container.block_item(item_id, "moderation", AdminContext(uuid7(), "k-lock-001", "тест"))
    assert stale is not None
    stale.block("other", None)
    async with container.uow() as uow2:
        with pytest.raises(ConflictError):
            await uow2.items.save(stale)


async def test_external_ref_taken_by_other_item(container: CatalogContainer) -> None:
    await upsert(container, film(tmdb="1", title="Брат"))
    item = film(tmdb="2", title="Брат 2")
    item.external_refs[ExternalSource.SEED] = "x"
    await upsert(container, item)
    clash = film(tmdb="3", title="Брат 3")
    clash.external_refs[ExternalSource.SEED] = "x"
    # SEED совпал с единицей «Брат 2» → это она и обновляется, а TMDB 3 конфликтов не даёт.
    assert await upsert(container, clash) is ItemOutcome.UPDATED
    stolen = film(tmdb="1", title="Брат")
    stolen.external_refs[ExternalSource.SEED] = "x"
    with pytest.raises(ConflictError):
        await upsert(container, stolen)


async def test_manual_aliases_persist_across_import(container: CatalogContainer) -> None:
    await upsert(container, film())
    item_id = await item_id_by_tmdb(container, "157336")
    await container.replace_aliases(
        item_id,
        [AliasDraft("Интерстеллар", AliasKind.TITLE, "ru"), AliasDraft("Интер", AliasKind.SHORT)],
        AdminContext(uuid7(), "k-alias-01", "тест"),
    )
    await upsert(container, film(year=2015))
    async with container.uow() as uow:
        item = await uow.items.get(item_id)
    assert item is not None
    sources = {a.normalized: a.source for a in item.aliases}
    assert sources["интер"] is AliasSource.MANUAL
    assert sources["интерстеллар"] is AliasSource.MANUAL


async def test_list_page_cursor_and_filters(container: CatalogContainer) -> None:
    for i in range(5):
        await upsert(container, film(tmdb=str(i), title=f"Брат {i}"))
    async with container.uow() as uow:
        first = await uow.items.list_page(AdminItemFilter(), None, 3)
        second = await uow.items.list_page(AdminItemFilter(), first.next_cursor, 3)
        filtered = await uow.items.list_page(
            AdminItemFilter(normalized_q="брат 4", domain=MediaDomain.FILM), None, 10
        )
        none = await uow.items.list_page(AdminItemFilter(status="blocked"), None, 10)
    assert len(first.items) == 3
    assert first.next_cursor is not None
    assert len(second.items) == 2
    assert second.next_cursor is None
    assert {i.id for i in first.items}.isdisjoint({i.id for i in second.items})
    assert [i.title for i in filtered.items] == ["Брат 4"]
    assert none.items == []


async def test_import_jobs_and_actions(container: CatalogContainer) -> None:
    actor = uuid7()
    job = ImportJob(
        uuid7(),
        source=ImportSource.FILE,
        ref=None,
        file_id="films.jsonl",
        actor_id=actor,
        reason="сиды",
        idempotency_key="imp-key-001",
    )
    async with container.uow() as uow:
        await uow.imports.add(job)
        await uow.commit()
    job.start()
    job.count(ItemOutcome.CREATED)
    async with container.uow() as uow:
        await uow.imports.save(job)
        await uow.commit()
    async with container.uow() as uow:
        loaded = await uow.imports.find_by_key(actor, "imp-key-001")
    assert loaded is not None
    assert loaded.counters.created == 1

    action = RecordedAction(uuid7(), actor, "act-key-01", "item.block", uuid7(), "r", "h" * 64, {})
    async with container.uow() as uow:
        await uow.actions.record(action)
        await uow.commit()
    async with container.uow() as uow:
        assert await uow.actions.find(actor, "act-key-01") == action
        with pytest.raises(ConflictError):
            await uow.actions.record(action)


async def test_clue_status_upsert(container: CatalogContainer) -> None:
    await upsert(container, film())
    item_id = await item_id_by_tmdb(container, "157336")
    status = ClueStatus(item_id, "poster_masked", "default", uuid7(), "ready", 1, 5)
    async with container.uow() as uow:
        await uow.clue_status.upsert(status)
        await uow.clue_status.upsert(dataclasses.replace(status, version=2, levels_count=6))
        await uow.commit()
    async with container.uow() as uow:
        loaded = await uow.clue_status.get(item_id, "poster_masked", "default")
    assert loaded is not None
    assert (loaded.version, loaded.levels_count) == (2, 6)


# ------------------------------------------------------------------ trigram-fallback


async def test_trgm_autocomplete_and_search(container: CatalogContainer) -> None:
    await upsert(container, film())
    await upsert(container, film(tmdb="2", title="Ёжик в тумане", year=1975))
    await upsert(
        container,
        ImportedItem(
            kind=MediaKind.TRACK,
            title="Интернационал",
            persons=(PersonRef("name:x", "Хор", PersonRole.ARTIST),),
            external_refs={ExternalSource.SPOTIFY: "s1"},
        ),
    )
    trgm = TrgmSearch(container.infra.sessions)  # type: ignore[arg-type]

    hits = await trgm.autocomplete(SearchQuery("инте", None, 10))
    assert {h.title for h in hits} == {"Интерстеллар", "Интернационал"}
    films = await trgm.autocomplete(SearchQuery("инте", MediaDomain.FILM, 10))
    assert [h.title for h in films] == ["Интерстеллар"]
    assert films[0].subtitle == "Interstellar"
    # Префикс слова в середине и транслит.
    assert [h.title for h in await trgm.autocomplete(SearchQuery("туман", None, 5))] == [
        "Ёжик в тумане"
    ]
    # Совпадения префикса ранжируются выше нечётких.
    translit = await trgm.autocomplete(SearchQuery("interst", None, 5))
    assert translit[0].title == "Интерстеллар"
    # Опечатка — нечёткий поиск.
    assert "Интерстеллар" in {
        h.title for h in await trgm.search(SearchQuery("интерстелар", None, 5))
    }
    # LIKE-спецсимволы не работают как шаблон.
    assert await trgm.autocomplete(SearchQuery("%%", None, 5)) == []

    item_id = await item_id_by_tmdb(container, "157336")
    await container.block_item(item_id, "moderation", AdminContext(uuid7(), "k-trgm-01", "тест"))
    for found in (
        await trgm.autocomplete(SearchQuery("интерс", None, 5)),
        await trgm.search(SearchQuery("интерстеллар", None, 5)),
    ):
        assert item_id not in {h.id for h in found}


# ------------------------------------------------------------------ потребитель событий


async def _process(infra: Infrastructure, envelope_bytes: bytes) -> Outcome:
    assert infra.sessions is not None and infra.tables is not None
    processor = EventProcessor(
        group="catalog.consumer",
        registry=build_event_handlers(infra),
        session_factory=infra.sessions,
        inbox=Inbox(infra.tables.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=FakeClock(),
    )
    return await processor.process(envelope_bytes, source="test")


async def test_own_events_indexed_idempotently(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    index = FakeSearch()
    container.search_index = index  # type: ignore[assignment]
    await upsert(container, film())
    item_id = await item_id_by_tmdb(container, "157336")
    await container.block_item(item_id, "moderation", AdminContext(uuid7(), "k-idx-0001", "т"))
    async with container.uow() as uow:
        created, blocked = await outbox_envelopes(uow.session, catalog_infra.tables.outbox)  # type: ignore[union-attr]

    assert await _process(catalog_infra, blocked.to_bytes()) is Outcome.PROCESSED
    assert index.docs[item_id].status == "blocked"
    assert index.docs[item_id].version == 2
    # Повтор того же события — inbox; старое событие — индекс не откатывается.
    assert await _process(catalog_infra, blocked.to_bytes()) is Outcome.DUPLICATE
    assert await _process(catalog_infra, created.to_bytes()) is Outcome.PROCESSED
    assert index.docs[item_id].status == "blocked"


async def test_assets_events_from_contract_examples(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    await upsert(container, film())
    item_id = await item_id_by_tmdb(container, "157336")
    ready = load_example("media_pipeline.AssetsReady")
    ready = ready.model_copy(
        update={"payload": {**ready.payload, "media_item_id": str(item_id), "version": 2}}
    )
    stale = load_example("media_pipeline.AssetsReady")
    stale = stale.model_copy(
        update={
            "event_id": uuid7(),
            "payload": {**stale.payload, "media_item_id": str(item_id), "version": 1},
        }
    )
    failed = load_example("media_pipeline.AssetsFailed")
    failed = failed.model_copy(
        update={
            # У примеров контракта общий event_id — иначе inbox счёл бы событие дублем.
            "event_id": uuid7(),
            "payload": {**failed.payload, "media_item_id": str(item_id)},
        }
    )

    for envelope in (ready, stale, failed):
        assert await _process(catalog_infra, envelope.to_bytes()) is Outcome.PROCESSED

    async with container.uow() as uow:
        poster = await uow.clue_status.get(item_id, "poster_masked", "default")
        frame = await uow.clue_status.get(item_id, "frame_progressive", "default")
    assert poster is not None and poster.version == 2 and poster.levels_count == 5
    assert frame is not None and frame.status == "failed"
    assert frame.error_code == "source.unavailable"


async def test_bad_asset_payload_goes_to_dlq(catalog_infra: Infrastructure) -> None:
    bad = load_example("media_pipeline.AssetsReady")
    bad = bad.model_copy(update={"payload": {**bad.payload, "version": "x"}})
    assert await _process(catalog_infra, bad.to_bytes()) is Outcome.DEAD


# ------------------------------------------------------------------ Redis


async def test_redis_prefix_cache_generation(catalog_infra: Infrastructure) -> None:
    from catalog_service.application.dto import Suggestion

    assert catalog_infra.redis is not None
    cache = RedisPrefixCache(catalog_infra.redis, prefix=f"cattest-{uuid7().hex[-12:]}", ttl_s=60)
    hit = Suggestion(uuid7(), MediaDomain.FILM, "Брат", 1997, None)
    assert await cache.get("all:10:бр") is None
    await cache.set("all:10:бр", [hit])
    assert await cache.get("all:10:бр") == [hit]
    await cache.invalidate()
    assert await cache.get("all:10:бр") is None


async def test_redis_rate_limiter(catalog_infra: Infrastructure) -> None:
    assert catalog_infra.redis is not None
    limiter = RedisRateLimiter(
        catalog_infra.redis, FakeClock(), prefix=f"cattest-{uuid7().hex[-12:]}", limit_per_minute=2
    )
    assert [await limiter.hit("ip") for _ in range(3)] == [True, True, False]
    assert await limiter.hit("other-ip")


async def test_outbox_rows_use_own_topic(container: CatalogContainer) -> None:
    await upsert(container, film())
    outbox = container.infra.tables.outbox  # type: ignore[union-attr]
    async with container.uow() as uow:
        topics = (await uow.session.execute(select(outbox.c.topic))).scalars().all()
    assert set(topics) == {"catalog.media_item.v1"}
