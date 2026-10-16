"""Миграции применяются на чистую базу и совпадают с метаданными (нет «дрейфа» схемы)."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core import uuid7
from gv_db.migrations import alembic_config, run_migrations
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration


async def test_migrations_apply_and_match_metadata(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), TABLES.metadata)
            )
    finally:
        await engine.dispose()
    assert diff == [], f"миграции расходятся с метаданными: {diff}"


async def test_0003_hashes_existing_subjects_and_downgrades(database_url: str) -> None:
    """0003: старые строки журнала получают sha256 субъекта; откат и повторный подъём работают."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0002")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO link_issuance (id, asset_set_id, level, subject, issued_at, "
                    "expires_at) VALUES (:id, :set, 1, 'game:42', now(), now())"
                ),
                {"id": uuid7(), "set": uuid7()},
            )
        await asyncio.to_thread(run_migrations, path, database_url)
        async with engine.connect() as conn:
            stored = (await conn.execute(text("SELECT subject_hash FROM link_issuance"))).scalar()
        assert stored == hashlib.sha256(b"game:42").hexdigest()

        await asyncio.to_thread(command.downgrade, config, "0002")
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


async def test_0004_adds_nullable_client_id_with_check(database_url: str) -> None:
    """0004: старые строки журнала остаются с client_id = NULL, мусор отвергается CHECK."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0003")
    insert = text(
        "INSERT INTO link_issuance (id, asset_set_id, level, subject_hash, issued_at, expires_at"
        "{col}) VALUES (:id, :set, 1, :hash, now(), now(){val})"
    )
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(insert.text.format(col="", val="")),
                {"id": uuid7(), "set": uuid7(), "hash": "0" * 64},
            )
        await asyncio.to_thread(run_migrations, path, database_url)
        with_client = text(insert.text.format(col=", client_id", val=", :client"))
        async with engine.begin() as conn:
            await conn.execute(
                with_client, {"id": uuid7(), "set": uuid7(), "hash": "1" * 64, "client": "game"}
            )
            rows = (
                (
                    await conn.execute(
                        text("SELECT client_id FROM link_issuance ORDER BY subject_hash")
                    )
                )
                .scalars()
                .all()
            )
        assert rows == [None, "game"]
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    with_client, {"id": uuid7(), "set": uuid7(), "hash": "2" * 64, "client": "Bad!"}
                )
        await asyncio.to_thread(command.downgrade, config, "0003")
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


async def test_0005_keeps_stage2_sources_and_accepts_new_kinds(database_url: str) -> None:
    """0005: старые исходники получают position = 1, новые виды и text принимаются,
    текстовое содержимое у нетекстового исходника отвергается; откат на чистых данных."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0004")
    item, old = uuid7(), uuid7()
    engine = create_async_engine(database_url)
    insert_source = (
        "INSERT INTO source_media (id, media_item_id, kind, origin, uri, status, created_at{col}) "
        "VALUES (:id, :item, :kind, :origin, 'https://x/y', 'registered', now(){val})"
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO media_items_state (media_item_id, domain, kind, status, version, "
                    "updated_at) VALUES (:id, 'film', 'film', 'active', 1, now())"
                ),
                {"id": item},
            )
            await conn.execute(
                text(insert_source.format(col="", val="")),
                {"id": old, "item": item, "kind": "frame", "origin": "tmdb"},
            )
        await asyncio.to_thread(run_migrations, path, database_url)
        async with engine.begin() as conn:
            position = (
                await conn.execute(
                    text("SELECT position FROM source_media WHERE id = :id"), {"id": old}
                )
            ).scalar()
            assert position == 1
            for kind, origin in (("soundtrack", "seed"), ("audio_clip", "upload")):
                await conn.execute(
                    text(insert_source.format(col=", position", val=", 3")),
                    {"id": uuid7(), "item": item, "kind": kind, "origin": origin},
                )
            await conn.execute(
                text(
                    "INSERT INTO source_media (id, media_item_id, kind, origin, status, sha256, "
                    "bytes, mime, created_at, text_content) VALUES (:id, :item, 'text', 'tmdb', "
                    "'ready', :sha, 10, 'application/json', now(), CAST(:content AS jsonb))"
                ),
                {"id": uuid7(), "item": item, "sha": "0" * 64, "content": '{"emoji": "x"}'},
            )
        for col, val in (
            (", text_content", ", CAST('{}' AS jsonb)"),  # содержимое у кадра
            (", position", ", 0"),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(insert_source.format(col=col, val=val)),
                        {"id": uuid7(), "item": item, "kind": "frame", "origin": "tmdb"},
                    )
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM source_media WHERE id <> :id"), {"id": old})
        await asyncio.to_thread(command.downgrade, config, "0004")
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


async def test_0006_dynamic_profiles_up_and_down(database_url: str) -> None:
    """0006: новая таблица и индекс; откат и повторный подъём."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url)
    engine = create_async_engine(database_url)
    row = (
        "INSERT INTO asset_profiles_dynamic (id, clue_type, domain, params, params_hash, status, "
        "error_code, request_id, mode_key, actor_id, created_at, updated_at) VALUES (:id, "
        "'emoji', 'film', CAST('{}' AS jsonb), :hash, :status, :code, :id, 'film.random.emoji', "
        ":id, now(), now())"
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(row), {"id": uuid7(), "hash": "a" * 64, "status": "active", "code": None}
            )
        with pytest.raises(IntegrityError):  # отклонённый без кода
            async with engine.begin() as conn:
                await conn.execute(
                    text(row),
                    {"id": uuid7(), "hash": "b" * 64, "status": "rejected", "code": None},
                )
        await asyncio.to_thread(command.downgrade, config, "0005")
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


async def test_0007_scoped_assets_keep_catalog_invariant_and_downgrade(database_url: str) -> None:
    """0007 (ADR 0029): строки этапа 3a остаются каталожными (scope NULL) и по-прежнему
    уникальны по профилю; набор сборника того же профиля допустим, второй — нет; CHECK
    отвергают половину scope и scope у нетекстового исходника; откат удаляет scoped-строки и
    возвращает прежнюю UNIQUE."""
    path = DEFINITION.migrations_path
    assert path is not None
    config = alembic_config(path, database_url)
    await asyncio.to_thread(run_migrations, path, database_url, "0006")
    item, frame, text_source, catalog_set = uuid7(), uuid7(), uuid7(), uuid7()
    collection = uuid7()
    insert_set = (
        "INSERT INTO asset_sets (id, media_item_id, clue_type, profile_key, params, params_hash, "
        "levels_count, status, generation, version, created_at, updated_at{col}) VALUES (:id, "
        ":item, 'emoji', 'default', CAST('{{}}' AS jsonb), :hash, 6, 'pending', 1, 1, now(), "
        "now(){val})"
    )
    insert_text = (
        "INSERT INTO source_media (id, media_item_id, kind, origin, status, sha256, bytes, mime, "
        "created_at, text_content{col}) VALUES (:id, :item, 'text', :origin, 'ready', :sha, 10, "
        "'application/json', now(), CAST('{{\"emoji\": \"x\"}}' AS jsonb){val})"
    )
    scoped = (", scope_kind, scope_id", ", 'collection', :scope")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO media_items_state (media_item_id, domain, kind, status, version, "
                    "updated_at) VALUES (:id, 'film', 'film', 'active', 1, now())"
                ),
                {"id": item},
            )
            await conn.execute(
                text(
                    "INSERT INTO source_media (id, media_item_id, kind, origin, uri, status, "
                    "created_at) VALUES (:id, :item, 'frame', 'tmdb', 'https://x/y', "
                    "'registered', now())"
                ),
                {"id": frame, "item": item},
            )
            await conn.execute(
                text(insert_set.format(col="", val="")),
                {"id": catalog_set, "item": item, "hash": "a" * 64},
            )
        await asyncio.to_thread(run_migrations, path, database_url)

        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text("SELECT scope_kind, scope_id FROM asset_sets UNION ALL "
                         "SELECT scope_kind, scope_id FROM source_media")
                )
            ).all()  # fmt: skip
            assert rows == [(None, None), (None, None)]
            await conn.execute(
                text(insert_text.format(col=scoped[0], val=scoped[1])),
                {"id": text_source, "item": item, "origin": "collection", "sha": "0" * 64,
                 "scope": collection},
            )  # fmt: skip
            await conn.execute(
                text(insert_set.format(col=scoped[0], val=scoped[1])),
                {"id": uuid7(), "item": item, "hash": "a" * 64, "scope": collection},
            )
        rejected: list[tuple[str, dict[str, object]]] = [
            # каталожный дубль профиля — прежний инвариант
            (insert_set.format(col="", val=""), {"hash": "a" * 64}),
            # второй набор того же профиля у того же сборника
            (insert_set.format(col=scoped[0], val=scoped[1]), {"hash": "a" * 64,
                                                               "scope": collection}),
            # половина scope
            (insert_set.format(col=", scope_id", val=", :scope"), {"hash": "b" * 64,
                                                                   "scope": collection}),
            # неизвестный вид владельца
            (insert_set.format(col=scoped[0], val=", 'user', :scope"), {"hash": "c" * 64,
                                                                        "scope": collection}),
        ]  # fmt: skip
        for statement, params in rejected:
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(text(statement), {"id": uuid7(), "item": item, **params})
        with pytest.raises(IntegrityError):  # scope у кадра
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO source_media (id, media_item_id, kind, origin, uri, status, "
                        "created_at, scope_kind, scope_id) VALUES (:id, :item, 'frame', 'tmdb', "
                        "'https://x/y', 'registered', now(), 'collection', :scope)"
                    ),
                    {"id": uuid7(), "item": item, "scope": collection},
                )
        with pytest.raises(IntegrityError):  # второй исходник сборника у единицы
            async with engine.begin() as conn:
                await conn.execute(
                    text(insert_text.format(col=scoped[0], val=scoped[1])),
                    {"id": uuid7(), "item": item, "origin": "collection", "sha": "1" * 64,
                     "scope": collection},
                )  # fmt: skip
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO collections_state (collection_id, version, status, updated_at) "
                    "VALUES (:id, 3, 'hidden', now())"
                ),
                {"id": collection},
            )

        await asyncio.to_thread(command.downgrade, config, "0006")
        async with engine.begin() as conn:
            assert (await conn.execute(text("SELECT id FROM asset_sets"))).scalars().all() == [
                catalog_set
            ]
            sources = (await conn.execute(text("SELECT id FROM source_media"))).scalars().all()
            assert sources == [frame]
        with pytest.raises(IntegrityError):  # прежняя UNIQUE вернулась
            async with engine.begin() as conn:
                await conn.execute(
                    text(insert_set.format(col="", val="")),
                    {"id": uuid7(), "item": item, "hash": "a" * 64},
                )
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()
