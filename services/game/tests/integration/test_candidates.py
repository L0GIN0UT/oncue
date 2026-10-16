"""SQL-адаптер ``CandidatePort`` фильтрует так же, как ``gv_testkit.game.FakeCandidatePort``.

Unit-тесты ядра и game опираются на фейк, поэтому семантика фильтров обязана совпадать:
один и тот же пул и набор запросов дают одинаковых кандидатов в порядке ``id``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from game_service.domain.projection import (
    AssetStatus,
    CatalogItem,
    ClueAssets,
    ItemStatus,
    catalog_metrics,
)
from game_service.infrastructure.db.repositories import SqlCandidatePort, SqlProjectionRepository
from game_service.main import DEFINITION
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_game_kernel import PersonRef, PlayableFor, PoolQuery, SubjectQuery
from gv_testkit import apply_migrations
from gv_testkit.game import FakeCandidatePort

pytestmark = pytest.mark.integration

HASH = "a" * 64
OTHER_HASH = "b" * 64
REVEAL_FOR = PlayableFor("frame_sequence", "c" * 64)


DIRECTOR = PersonRef(id=uuid7(), name="Кристофер Нолан", role="director")
ACTOR = PersonRef(id=uuid7(), name="Кристиан Бейл", role="actor")


def _item(
    *,
    domain: str = "film",
    kind: str = "film",
    year: int | None = 2000,
    genres: tuple[str, ...] = ("drama",),
    popularity: float | None = 0.5,
    persons: tuple[PersonRef, ...] | None = None,
    metrics: dict[str, float] | None = None,
) -> CatalogItem:
    item_id = uuid7()
    return CatalogItem(
        media_item_id=item_id,
        domain=domain,
        kind=kind,
        status=ItemStatus.ACTIVE,
        version=1,
        title=f"Единица {item_id.hex[-6:]}",
        year=year,
        aliases=("единица", "unit", "edinitsa"),
        title_aliases=("единица", "unit"),
        persons=persons
        if persons is not None
        else (PersonRef(id=uuid7(), name="Режиссёр", role="director"),),
        genres=genres,
        popularity=popularity,
        metrics=catalog_metrics(
            metrics if metrics is not None else {"vote_average": 7.0}, year=year
        ),
    )


POOL = [
    _item(),
    _item(year=None),
    _item(year=1985, genres=("rock", "drama")),
    _item(genres=()),
    _item(popularity=None),
    _item(popularity=0.9, genres=("comedy",)),
    _item(kind="series"),
    _item(domain="music", kind="track", genres=("rock",)),
    _item(persons=(DIRECTOR, ACTOR), metrics={}),
    _item(persons=(DIRECTOR,), metrics={"revenue_usd": 100}),
    _item(persons=(DIRECTOR, ACTOR)),
    _item(persons=(ACTOR,), year=None),
]
PLAYABLE = {
    POOL[0].media_item_id,
    POOL[2].media_item_id,
    POOL[5].media_item_id,
    POOL[8].media_item_id,
    POOL[9].media_item_id,
    POOL[10].media_item_id,
}
# Второй набор (ревил другого типа) — только у части играбельных.
REVEAL = {POOL[0].media_item_id, POOL[9].media_item_id, POOL[10].media_item_id}


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        repo = SqlProjectionRepository(session)
        for item in POOL:
            await repo.upsert_item(item)
            ready = item.media_item_id in PLAYABLE
            await repo.upsert_assets(
                ClueAssets(
                    media_item_id=item.media_item_id,
                    clue_type="frame_progressive",
                    params_hash=HASH if ready else OTHER_HASH,
                    asset_set_id=uuid7(),
                    levels_count=6,
                    status=AssetStatus.READY,
                    version=1,
                )
            )
            if item.media_item_id in REVEAL:
                await repo.upsert_assets(
                    ClueAssets(
                        media_item_id=item.media_item_id,
                        clue_type=REVEAL_FOR.clue_type,
                        params_hash=REVEAL_FOR.params_hash,
                        asset_set_id=uuid7(),
                        levels_count=1,
                        status=AssetStatus.READY,
                        version=2,
                    )
                )
    yield factory
    await engine.dispose()


QUERIES = [
    PoolQuery(domain="film"),
    PoolQuery(domain="film", kinds=frozenset({"film"})),
    PoolQuery(domain="film", genres_any=frozenset({"drama", "comedy"})),
    PoolQuery(domain="film", year_from=1990),
    PoolQuery(domain="film", year_to=1990),
    PoolQuery(domain="film", year_from=1980, year_to=2001),
    PoolQuery(domain="film", min_popularity=0.6),
    PoolQuery(domain="film", exclude_ids=frozenset({POOL[0].media_item_id})),
    PoolQuery(domain="film", playable_for=PlayableFor("frame_progressive", HASH)),
    PoolQuery(domain="music"),
    PoolQuery(domain="film", person_ids_any=frozenset({DIRECTOR.id})),
    PoolQuery(domain="film", person_ids_any=frozenset({ACTOR.id, uuid7()})),
    PoolQuery(domain="film", has_metric="vote_average"),
    PoolQuery(domain="film", has_metric="year"),
    PoolQuery(domain="film", has_metric="revenue_usd", person_ids_any=frozenset({DIRECTOR.id})),
]


@pytest.mark.parametrize("query", QUERIES)
async def test_sql_candidates_match_fake_port(
    sessions: async_sessionmaker[AsyncSession], query: PoolQuery
) -> None:
    fake = FakeCandidatePort(
        [i.to_ref() for i in POOL],
        playable=[(i, "frame_progressive", HASH) for i in PLAYABLE],
    )
    async with sessions() as session:
        found = await SqlCandidatePort(SqlProjectionRepository(session), 100).candidates(query, 100)
    assert [r.id for r in found] == [r.id for r in await fake.candidates(query, 100)]


async def test_candidates_are_ordered_by_id_and_limited(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        port = SqlCandidatePort(SqlProjectionRepository(session), 3)
        found = await port.candidates(PoolQuery(domain="film"), 100)
    ids: list[UUID] = [r.id for r in found]
    assert len(ids) == 3 and ids == sorted(ids)


async def test_candidate_refs_carry_title_aliases(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        port = SqlCandidatePort(SqlProjectionRepository(session), 100)
        found = await port.candidates(PoolQuery(domain="film"), 100)
    ref = next(r for r in found if r.id == POOL[0].media_item_id)
    assert ref.title_aliases == ("единица", "unit")
    assert ref.aliases == ("единица", "unit", "edinitsa")
    assert ref.persons == POOL[0].persons


async def test_second_asset_set_filters_candidates_and_count(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Ревил другого типа (ADR 0026): единица играбельна, только если готовы оба набора."""
    query = PoolQuery(domain="film", playable_for=PlayableFor("frame_progressive", HASH))
    async with sessions() as session:
        repo = SqlProjectionRepository(session)
        found = await SqlCandidatePort(repo, 100, REVEAL_FOR).candidates(query, 100)
        count = await repo.count_candidates(query, also=REVEAL_FOR)
    assert {r.id for r in found} == REVEAL
    assert count == len(REVEAL)


SUBJECT_QUERIES = [
    (SubjectQuery(domain="film", roles=frozenset({"director"}), min_items=1), ""),
    (SubjectQuery(domain="film", roles=frozenset({"director", "actor"}), min_items=2), ""),
    (SubjectQuery(domain="film", roles=frozenset({"actor"}), min_items=1), "бейл"),
    (SubjectQuery(domain="film", roles=frozenset({"actor"}), min_items=1), "нолан"),
    (
        SubjectQuery(
            domain="film",
            roles=frozenset({"director"}),
            min_items=2,
            playable_for=PlayableFor("frame_progressive", HASH),
        ),
        "",
    ),
    (SubjectQuery(domain="film", roles=frozenset({"director"}), min_items=4), ""),
    (SubjectQuery(domain="music", roles=frozenset({"artist"}), min_items=1), ""),
]


@pytest.mark.parametrize(("query", "text"), SUBJECT_QUERIES)
async def test_sql_subjects_match_fake_port(
    sessions: async_sessionmaker[AsyncSession], query: SubjectQuery, text: str
) -> None:
    """B7: поиск персон марафона и темы сетки — та же семантика, что у фейка ядра."""
    fake = FakeCandidatePort(
        [i.to_ref() for i in POOL],
        playable=[(i, "frame_progressive", HASH) for i in PLAYABLE],
    )
    expected = [(h.person.id, h.person.role, h.items) for h in fake.subjects(query, text, 20)]
    async with sessions() as session:
        rows = await SqlProjectionRepository(session).subjects(query, text_=text or None, limit=20)
    assert [(r.person_id, r.role, r.items) for r in rows] == expected


async def test_subjects_limit_and_projection_columns(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    query = SubjectQuery(domain="film", roles=frozenset({"director", "actor"}), min_items=1)
    async with sessions() as session:
        repo = SqlProjectionRepository(session)
        rows = await repo.subjects(query, text_=None, limit=1)
        item = await repo.get_item(POOL[8].media_item_id)
    # Ничья по числу единиц (3 и 3) решается по имени: «Кристиан» < «Кристофер».
    assert len(rows) == 1 and (rows[0].person_id, rows[0].items) == (ACTOR.id, 3)
    assert item is not None and item.person_ids == (DIRECTOR.id, ACTOR.id)
    assert item.metric("year") == 2000.0 and item.metric("vote_average") is None
