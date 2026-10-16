"""Каталог на Postgres (K6, D10): порядок сортировок, keyset без дублей, план с индексами."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from collections_service.application.ports import CatalogQuery
from collections_service.application.queries.collections import sort_value
from collections_service.infrastructure.db.repositories import (
    SqlCatalogReads,
    _tsvector,
    catalog_statement,
)
from collections_service.infrastructure.db.tables import collections
from collections_service.main import DEFINITION
from sqlalchemy import insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from gv_core.ids import uuid7
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


def _row(n: int, rng: random.Random, **extra: Any) -> dict[str, Any]:
    title = extra.pop("title", f"Сборник {n}")
    description = extra.pop("description", "")
    return {
        "id": uuid7(),
        "domain": rng.choice(["film", "music"]),
        "kind": "ugc",
        "status": "published",
        "visibility": "public",
        "author_id": uuid7(),
        "version": 1,
        "content_version": 1,
        "title": title,
        "description": description,
        "default_mode_key": "film.collection.frame",
        "items_count": 5,
        "likes_count": rng.randrange(0, 50),
        "popular_score": float(rng.randrange(0, 20)),
        "difficulty_score": rng.choice([None, round(rng.random(), 2)]),
        "created_at": NOW,
        "updated_at": NOW,
        "first_published_at": NOW - timedelta(minutes=rng.randrange(0, 60)),
        "published_at": NOW,
        **extra,
    }


async def _fill(engine: AsyncEngine, rows: list[dict[str, Any]]) -> None:
    """Пакетная вставка; ``title_tsv`` — тем же выражением, что пишет репозиторий."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    async with engine.begin() as conn:
        for group in groups.values():
            await conn.execute(insert(collections), group)
        for row in rows:
            if row["title"].startswith("Сборник "):
                continue  # фоновые строки: tsvector для сортировок не нужен
            await conn.execute(
                collections.update()
                .where(collections.c.id == row["id"])
                .values(title_tsv=_tsvector(row["title"], row["description"]))
            )
        await conn.execute(
            text(
                "UPDATE collections SET title_tsv = to_tsvector('simple', title) "
                "WHERE title_tsv IS NULL"
            )
        )
        await conn.execute(text("ANALYZE collections"))


async def _pages(engine: AsyncEngine, sort: str, limit: int, **filters: Any) -> list[Any]:
    seen: list[Any] = []
    after = None
    async with AsyncSession(engine) as session:
        reads = SqlCatalogReads(session)
        while True:
            rows = await reads.search(CatalogQuery(sort=sort, limit=limit, after=after, **filters))
            seen += rows
            if len(rows) < limit:
                return seen
            after = (sort_value(sort, rows[-1]), rows[-1].collection_id)


@pytest.mark.parametrize("sort", ["popular", "new", "likes", "difficulty"])
async def test_keyset_pages_follow_sort_without_duplicates(engine: AsyncEngine, sort: str) -> None:
    rng = random.Random(7)
    rows = [_row(n, rng) for n in range(120)]
    rows += [
        _row(1000, rng, visibility="unlisted"),
        _row(1001, rng, kind="curated", author_id=None),
    ]
    rows.append({**_row(1002, rng), "status": "hidden", "hidden_at": NOW, "hidden_reason": "спам"})
    await _fill(engine, rows)
    seen = await _pages(engine, sort, 7)
    ids = [r.collection_id for r in seen]
    assert len(ids) == len(set(ids)) == 121  # без unlisted и скрытого
    keys = [(sort_value(sort, r), r.collection_id) for r in seen]
    assert keys == sorted(keys, reverse=True)
    if sort == "difficulty":
        nulls = [r.difficulty_score is None for r in seen]
        assert nulls == sorted(nulls)  # без сложности — в конце


async def test_filters_and_text_search(engine: AsyncEngine) -> None:
    rng = random.Random(3)
    rows = [_row(n, rng) for n in range(30)]
    target = _row(99, rng, title="Ужасы 80-х", description="кассеты и VHS", domain="film")
    curated = _row(98, rng, kind="curated", author_id=None, title="Лучшее кино", domain="film")
    rows += [target, curated]
    await _fill(engine, rows)

    async def ids(**kw: Any) -> set[Any]:
        return {r.collection_id for r in await _pages(engine, "new", 50, **kw)}

    assert await ids(text="ужас") == {target["id"]}
    assert await ids(text="кассета") == {target["id"]}  # описание, стемминг
    assert await ids(text="80-х") == {target["id"]}
    assert await ids(text="Уж") == {target["id"]}  # подстрока названия (pg_trgm, ILIKE)
    assert await ids(text="ужасов") == {target["id"]}  # другая словоформа
    assert await ids(curated=True) == {curated["id"]}
    music = {r["id"] for r in rows if r["domain"] == "music"}
    assert await ids(domain="music") == music
    hard = {
        r["id"] for r in rows if r["difficulty_score"] is not None and r["difficulty_score"] >= 0.7
    }
    assert await ids(difficulty=(0.7, 2.0)) == hard


def _sql(query: CatalogQuery) -> str:
    compiled = catalog_statement(query).compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call] — фабрика диалекта без аннотаций
        compile_kwargs={"literal_binds": True},
    )
    # literal_binds экранирует % для pyformat; EXPLAIN уходит через text() — вернуть как было.
    return str(compiled).replace("%%", "%")


@pytest.mark.parametrize(
    ("query", "index"),
    [
        (CatalogQuery(sort="popular", limit=21), "ix_collections_catalog_popular"),
        (CatalogQuery(sort="new", limit=21), "ix_collections_catalog_new"),
        (CatalogQuery(sort="likes", limit=21), "ix_collections_catalog_likes"),
        (CatalogQuery(sort="difficulty", limit=21), "ix_collections_catalog_difficulty"),
        (CatalogQuery(sort="featured", limit=21), "ix_collections_catalog_featured"),
        (
            CatalogQuery(sort="popular", limit=21, after=(5.0, uuid7())),
            "ix_collections_catalog_popular",
        ),
        (CatalogQuery(sort="popular", limit=21, text="редкий"), "ix_collections_title"),
    ],
)
async def test_explain_uses_indexes_on_10k_rows(
    engine: AsyncEngine, query: CatalogQuery, index: str
) -> None:
    rng = random.Random(11)
    rows = [_row(n, rng) for n in range(10_000)]
    rows[5000] = _row(5000, rng, title="Редкий сборник")
    for row in rows[:50]:
        row["featured"], row["featured_rank"] = True, rng.randrange(1, 100)
    await _fill(engine, rows)
    async with engine.begin() as conn:
        if query.text:
            # На 10 тыс. узких строк последовательное чтение дешевле любого индекса; проверяем,
            # что условие поиска индексируемо целиком (BitmapOr по GIN), а не планировщик.
            await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join((await conn.execute(text("EXPLAIN " + _sql(query)))).scalars())
    assert index in plan, plan
    assert "Seq Scan on collections" not in plan, plan
