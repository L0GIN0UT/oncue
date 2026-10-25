"""SQL-адаптер ``CollectionPort`` (проекция сборников) совпадает с
``gv_testkit.game.FakeCollectionPort.matches`` (ADR 0028, 0029).

Порядок — по ``position``, только активные единицы каталога, при ``playable_for`` —
набор со ``scope`` сборника или каталожный. Наборы сборника не попадают в каталожные
выборки (случайный режим, дейлик), а для своей сессии берутся первыми.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from game_service.domain.projection import (
    AssetStatus,
    CatalogItem,
    ClueAssets,
    CollectionItem,
    CollectionProj,
    CollectionStatus,
    ItemStatus,
    catalog_metrics,
)
from game_service.infrastructure.db.repositories import (
    SqlCollectionPort,
    SqlCollectionRepository,
    SqlProjectionRepository,
)
from game_service.main import DEFINITION
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_game_kernel import PlayableFor, PoolQuery
from gv_testkit import apply_migrations
from gv_testkit.game import FakeCollectionPort

pytestmark = pytest.mark.integration

CLUE = "emoji"
HASH = "e" * 64
REVEAL = PlayableFor("frame_progressive", "f" * 64)
AT = datetime(2026, 10, 16, tzinfo=UTC)


def _item(status: ItemStatus = ItemStatus.ACTIVE) -> CatalogItem:
    item_id = uuid7()
    return CatalogItem(
        media_item_id=item_id,
        domain="film",
        kind="film",
        status=status,
        version=1,
        title=f"Единица {item_id.hex[-6:]}",
        year=2000,
        aliases=("единица",),
        title_aliases=("единица",),
        persons=(),
        genres=("drama",),
        popularity=0.5,
        metrics=catalog_metrics({}, year=2000),
    )


ITEMS = [_item() for _ in range(6)] + [_item(ItemStatus.BLOCKED)]
IDS = [i.media_item_id for i in ITEMS]
CID, OTHER = uuid7(), uuid7()
# Порядок автора не совпадает с порядком id: адаптер обязан сортировать по position.
ORDER = [IDS[3], IDS[0], IDS[6], IDS[5], IDS[1], IDS[2], IDS[4]]
CATALOG = {IDS[0], IDS[1], IDS[5], IDS[6]}  # каталожный набор emoji
OWN = {IDS[2], IDS[3]}  # свой набор этого сборника (эмодзи автора)
FOREIGN = {IDS[4]}  # набор чужого сборника — сюда не годится
REVEAL_READY = {IDS[0], IDS[3], IDS[5]}


def _assets(item: UUID, scope: UUID | None, clue: str = CLUE, hash_: str = HASH) -> ClueAssets:
    return ClueAssets(
        media_item_id=item,
        clue_type=clue,
        params_hash=hash_,
        asset_set_id=uuid7(),
        levels_count=6,
        status=AssetStatus.READY,
        version=1,
        scope_id=scope,
    )


def _proj(cid: UUID, status: CollectionStatus = CollectionStatus.PUBLISHED) -> CollectionProj:
    return CollectionProj(
        collection_id=cid,
        version=1,
        content_version=1,
        domain="film",
        status=status,
        visibility="public",
        author_id=uuid7(),
        updated_at=AT,
    )


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        repo = SqlProjectionRepository(session)
        for item in ITEMS:
            await repo.upsert_item(item)
        for item_id in CATALOG:
            await repo.upsert_assets(_assets(item_id, None))
        for item_id in OWN:
            await repo.upsert_assets(_assets(item_id, CID))
        for item_id in FOREIGN:
            await repo.upsert_assets(_assets(item_id, OTHER))
        for item_id in REVEAL_READY:
            await repo.upsert_assets(_assets(item_id, None, REVEAL.clue_type, REVEAL.params_hash))
        collections = SqlCollectionRepository(session)
        items = [CollectionItem(position=p, media_item_id=i) for p, i in enumerate(ORDER)]
        await collections.save(_proj(CID), items)
        await collections.save(_proj(OTHER), [CollectionItem(position=0, media_item_id=IDS[4])])
    yield factory
    await engine.dispose()


def _fake() -> FakeCollectionPort:
    fake = FakeCollectionPort(
        {CID: [i.to_ref() for i in sorted(ITEMS, key=lambda x: ORDER.index(x.media_item_id))]},
        playable=[(i, CLUE, HASH) for i in CATALOG],
        scoped=[(CID, i, CLUE, HASH) for i in OWN] + [(OTHER, i, CLUE, HASH) for i in FOREIGN],
    )
    fake.block(IDS[6])
    return fake


@pytest.mark.parametrize(
    "playable_for",
    [
        None,
        PlayableFor(CLUE, HASH),
        PlayableFor(CLUE, HASH, scope=CID),
        PlayableFor(CLUE, HASH, scope=OTHER),
        PlayableFor(CLUE, "0" * 64, scope=CID),
    ],
)
@pytest.mark.parametrize("limit", [100, 2])
async def test_sql_collection_port_matches_fake(
    sessions: async_sessionmaker[AsyncSession], playable_for: PlayableFor | None, limit: int
) -> None:
    async with sessions() as session:
        port = SqlCollectionPort(SqlCollectionRepository(session), 100)
        found = await port.items(CID, limit, playable_for=playable_for)
    expected = await _fake().items(CID, limit, playable_for=playable_for)
    assert [r.id for r in found] == [r.id for r in expected]


async def test_own_set_counts_for_reveal_too_and_hidden_collection_is_empty(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Второй набор ревила тоже обязателен; скрытый сборник адаптер не отдаёт."""
    need = PlayableFor(CLUE, HASH, scope=CID)
    async with sessions() as session, session.begin():
        collections = SqlCollectionRepository(session)
        found = await SqlCollectionPort(collections, 100, REVEAL).items(CID, 100, playable_for=need)
        assert [r.id for r in found] == [IDS[3], IDS[0], IDS[5]]
        assert await collections.total(CID) == 6  # заблокированная не считается
        hidden = _proj(CID, CollectionStatus.HIDDEN)
        await collections.save(hidden, [CollectionItem(0, IDS[0])])
        assert await SqlCollectionPort(collections, 100).items(CID, 100, playable_for=None) == []
        await session.rollback()


async def test_scoped_sets_stay_out_of_catalog_pools(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Свой набор сборника не делает единицу играбельной в каталожных режимах, а в своей
    сессии берётся первым (рядом с каталожным под те же параметры)."""
    query = PoolQuery(domain="film", playable_for=PlayableFor(CLUE, HASH))
    async with sessions() as session, session.begin():
        repo = SqlProjectionRepository(session)
        pool = {r.id for r in await repo.candidates(query, 100)}
        assert pool == CATALOG - {IDS[6]}
        assert await repo.count_candidates(query) == len(pool)
        own = _assets(IDS[0], CID)
        await repo.upsert_assets(own)
        catalog_only = await repo.ready_assets(IDS, CLUE, HASH)
        assert set(catalog_only) == CATALOG - {IDS[6]}
        assert all(a.scope_id is None for a in catalog_only.values())
        scoped = await repo.ready_assets(IDS, CLUE, HASH, scope=CID)
        assert set(scoped) == (CATALOG | OWN) - {IDS[6]}
        assert scoped[IDS[0]].asset_set_id == own.asset_set_id
        assert not await repo.has_assets(CLUE, "1" * 64)

        removed = await repo.delete_scoped_assets(CID, keep={IDS[2]})
        assert removed == 2  # IDS[0] и IDS[3]; IDS[2] остался, чужой сборник не тронут
        left = await repo.ready_assets(IDS, CLUE, HASH, scope=CID)
        assert left[IDS[2]].scope_id == CID and left[IDS[0]].scope_id is None
        assert IDS[3] not in left
        assert IDS[4] in await repo.ready_assets(IDS, CLUE, HASH, scope=OTHER)
        await session.rollback()


async def test_collection_tags_roundtrip_through_projection(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """ADR 0067: теги проекции сохраняются и читаются (по умолчанию - пусто)."""
    async with sessions() as session, session.begin():
        repo = SqlCollectionRepository(session)
        cid = uuid7()
        await repo.save(_proj(cid), [])
        plain = await repo.get(cid)
        await repo.save(replace(_proj(cid), tags=("new_year", "horror")), [])
        got = await repo.get(cid)
    assert plain is not None and plain.tags == ()
    assert got is not None and got.tags == ("new_year", "horror")
