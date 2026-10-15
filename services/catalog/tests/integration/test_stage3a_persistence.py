"""Этап 3a на реальном Postgres: тексты загадок, страны, исходники с происхождением,
переопубликация, миграция 0004 (план M7–M9)."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

import pytest
from alembic import command
from catalog_service.application.commands.admin import AdminContext, ClueTextsEdit
from catalog_service.application.commands.imports import UpsertImportedItem
from catalog_service.application.dto import ImportedClueTexts, ImportedItem, PersonRef
from catalog_service.domain.model import (
    ExternalSource,
    MediaDomain,
    MediaKind,
    PersonRole,
    SourceMediaDraft,
    SourceMediaKind,
    SourceMediaOrigin,
)
from catalog_service.infrastructure.container import CatalogContainer
from catalog_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7
from gv_db.migrations import alembic_config, run_migrations
from gv_testkit import InMemoryTaskQueue, assert_event_matches_schema, outbox_envelopes

pytestmark = pytest.mark.integration


def film(tmdb: str = "27205") -> ImportedItem:
    return ImportedItem(
        kind=MediaKind.FILM,
        title="Начало",
        original_title="Inception",
        year=2010,
        persons=(PersonRef("tmdb:525", "Кристофер Нолан", PersonRole.DIRECTOR),),
        external_refs={ExternalSource.TMDB: tmdb},
        countries=("GB", "US"),
        source_media=(
            SourceMediaDraft(
                SourceMediaKind.FRAME,
                SourceMediaOrigin.TMDB,
                "https://image.tmdb.org/t/p/original/f2.jpg",
                position=2,
                source_url="https://www.themoviedb.org/movie/27205",
                license_note="TMDB",
            ),
            SourceMediaDraft(
                SourceMediaKind.SOUNDTRACK, SourceMediaOrigin.UPLOAD, "https://x.test/s.m4a"
            ),
        ),
        clue_texts=ImportedClueTexts(plot_raw="Кобб крадёт идеи. «Начало» Нолана.", tagline="Сон"),
    )


async def test_clue_texts_countries_and_sources_roundtrip(container: CatalogContainer) -> None:
    upsert = UpsertImportedItem(container.uow, container.normalizer, container.infra.clock)
    await upsert(film())
    async with container.uow() as uow:
        item = await uow.items.find_by_external_ref(ExternalSource.TMDB, "27205")
        assert item is not None
        texts = await uow.clue_texts.get(item.id)
    assert item.countries == ("GB", "US")
    by_kind = {s.kind: s for s in item.source_media}
    frame = by_kind[SourceMediaKind.FRAME]
    assert (frame.position, frame.source_url, frame.license_note) == (
        2,
        "https://www.themoviedb.org/movie/27205",
        "TMDB",
    )
    assert by_kind[SourceMediaKind.SOUNDTRACK].position == 1
    assert texts is not None
    assert (texts.plot_masked, texts.revision, texts.status.value) == (
        "Кобб крадёт идеи. «…» ….",
        1,
        "draft",
    )
    assert item.clue_texts.is_empty

    ctx = AdminContext(uuid7(), f"k-{uuid7().hex}", "проверено модератором")
    await container.approve_clue_texts(item.id, 1, ctx)
    async with container.uow() as uow:
        stored = await uow.items.get(item.id)
    assert stored is not None
    assert stored.clue_texts.tagline == "Сон"
    assert stored.version == item.version + 1

    edit = ClueTextsEdit("Новый …", "Сон", "🚀🌙", base_revision=1)
    ctx2 = AdminContext(ctx.actor_id, f"k-{uuid7().hex}", "уточнил эмодзи")
    view = await container.edit_clue_texts(item.id, edit, ctx2)
    assert (view["status"], view["revision"], view["approved_revision"]) == ("draft", 2, 1)

    infra = container.infra
    assert infra.sessions is not None and infra.tables is not None
    async with infra.sessions() as session:
        envelopes = await outbox_envelopes(session, infra.tables.outbox)
    for envelope in envelopes:
        assert_event_matches_schema(envelope)
    last = envelopes[-1].payload
    assert last["clue_texts"] == {"plot_masked": "Кобб крадёт идеи. «…» ….", "tagline": "Сон"}
    assert last["countries"] == ["GB", "US"]


async def test_republish_job_runs_in_batches(container: CatalogContainer) -> None:
    container.tasks = InMemoryTaskQueue()  # type: ignore[assignment]  # фейк того же протокола
    upsert = UpsertImportedItem(container.uow, container.normalizer, container.infra.clock)
    for i in range(3):
        await upsert(dataclasses.replace(film(tmdb=f"r{i}"), clue_texts=None))
    ctx = AdminContext(uuid7(), f"k-{uuid7().hex}", "заполнить новые поля")
    response = await container.request_republish(MediaDomain.FILM, ctx)
    job_id = uuid.UUID(response["job_id"])
    batch = container.run_republish
    after: uuid.UUID | None = None
    total = 0
    queue = container.tasks
    assert isinstance(queue, InMemoryTaskQueue)
    while queue.tasks:
        task = queue.tasks.pop(0)
        raw = task["payload"]["after"]
        after = uuid.UUID(raw) if raw else None
        total += await batch(job_id, after)
    assert total == 3
    async with container.uow() as uow:
        job = await uow.republish.get(job_id)
        ids = await uow.items.list_ids_after(MediaDomain.FILM, None, 10)
    assert job is not None and (job.status.value, job.published) == ("done", 3)
    assert len(ids) == 3


async def test_0004_up_and_down_with_stage2_rows(database_url: str) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0003")
    item = uuid7()
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO media_items (id, domain, kind, status, title, version) "
                    "VALUES (:id, 'film', 'film', 'active', 'Старый фильм', 1)"
                ),
                {"id": item},
            )
            await conn.execute(
                text(
                    "INSERT INTO source_media_refs (id, media_item_id, kind, origin, uri, position)"
                    " VALUES (:id, :item, 'frame', 'tmdb', 'https://x/f.jpg', 0)"
                ),
                {"id": uuid7(), "item": item},
            )
        await asyncio.to_thread(run_migrations, path, database_url)
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text("SELECT countries, clue_texts FROM media_items WHERE id = :id"),
                    {"id": item},
                )
            ).one()
            assert (row.countries, row.clue_texts) == ([], None)
            kind_position = (
                await conn.execute(text("SELECT kind_position FROM source_media_refs"))
            ).scalar()
            assert kind_position == 1
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO clue_texts (media_item_id, status, revision, "
                        "approved_revision) VALUES (:id, 'approved', 1, 2)"
                    ),
                    {"id": item},
                )
        await asyncio.to_thread(command.downgrade, config, "0003")
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()
