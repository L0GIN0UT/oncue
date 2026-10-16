"""Миграции: вверх на чистую базу без дрейфа, вниз до 0001 и снова вверх; CHECK-и схемы."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from collections_service.infrastructure.db.tables import TABLES
from collections_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gv_core.ids import uuid7
from gv_db.migrations import alembic_config, run_migrations
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


async def test_migrations_apply_and_match_metadata(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), TABLES.metadata)
        )
    assert diff == [], f"миграции расходятся с метаданными: {diff}"


async def test_0002_downgrades_and_upgrades_again(database_url: str, engine: AsyncEngine) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO outbox (event_id, topic, key, event_type, payload, created_at) "
                "VALUES (:id, 't', 'k', 'e', '\\x00', now())"
            ),
            {"id": uuid7()},
        )
    await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0001")
    async with engine.connect() as conn:
        tables = set(
            (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            ).scalars()
        )
        outbox = (await conn.execute(text("SELECT count(*) FROM outbox"))).scalar()
    assert tables == {"outbox", "inbox", "alembic_version"}
    assert outbox == 1  # служебные таблицы и их данные откат не трогает
    await asyncio.to_thread(run_migrations, path, database_url)


BASE = (
    "INSERT INTO collections (id, domain, kind, status, author_id, title, created_at, "
    "updated_at, content_version, first_published_at, published_at, default_mode_key, "
    "hidden_at, deleted_at, featured, likes_count) VALUES "
)


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ("'anime','ugc','draft',:a,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,0", "domain_known"),
        (
            "'film','curated','draft',NULL,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,0",
            "curated_consistent",
        ),
        (
            "'film','curated','published',:a,'t',:n,:n,1,:n,:n,'film.collection.frame',NULL,NULL,"
            "false,0",
            "curated_consistent",
        ),
        (
            "'film','ugc','draft',NULL,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,0",
            "draft_has_author",
        ),
        (
            "'film','ugc','draft',:a,'t',:n,:n,1,NULL,NULL,NULL,NULL,NULL,false,0",
            "draft_unpublished",
        ),
        (
            "'film','ugc','published',:a,'t',:n,:n,1,:n,:n,NULL,NULL,NULL,false,0",
            "published_complete",
        ),
        (
            "'film','ugc','hidden',:a,'t',:n,:n,1,:n,:n,'film.collection.frame',NULL,NULL,false,0",
            "hidden_has_reason",
        ),
        (
            "'film','ugc','deleted',:a,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,0",
            "deleted_has_time",
        ),
        (
            "'film','ugc','draft',:a,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,true,0",
            "featured_published",
        ),
        (
            "'film','ugc','draft',:a,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,-1",
            "counters_non_negative",
        ),
        ("'film','ugc','draft',:a,'',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,0", "title_not_empty"),
    ],
)
async def test_checks_reject_inconsistent_rows(
    engine: AsyncEngine, values: str, constraint: str
) -> None:
    now = NOW
    with pytest.raises(IntegrityError, match=f"ck_collections_{constraint}"):
        async with engine.begin() as conn:
            await conn.execute(
                text(BASE + f"(:id, {values})"), {"id": uuid7(), "a": uuid7(), "n": now}
            )


async def test_valid_rows_and_child_checks(engine: AsyncEngine) -> None:
    now = NOW
    cid = uuid7()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                BASE + "(:id,'film','ugc','draft',:a,'t',:n,:n,0,NULL,NULL,NULL,NULL,NULL,false,0)"
            ),
            {"id": cid, "a": uuid7(), "n": now},
        )
    for statement, constraint in (
        ("INSERT INTO collection_items VALUES (:c, 200, :i, NULL)", "position_range"),
        (
            "INSERT INTO collection_drafts (collection_id, revision, body, updated_at) "
            "VALUES (:c, 0, '{}', now())",
            "revision_positive",
        ),
        ("INSERT INTO completions VALUES (:i, :c, 1, 1.5, :i, now())", "ratio_range"),
        (
            "INSERT INTO catalog_items_proj VALUES (:i, 'film', 'gone', 1, 't', NULL)",
            "status_known",
        ),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            async with engine.begin() as conn:
                await conn.execute(text(statement), {"c": cid, "i": uuid7()})
