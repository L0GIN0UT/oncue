"""Несколько сборников лобби (A5, D12 этапа 4): ``DrawContext.collection_ids`` в источнике
``collection`` — чередование сборников, без повторов единиц, детерминизм по seed, лимит
``max_collections``; один сборник — как на этапе 3b."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gv_game_kernel import (
    ALL_SESSIONS,
    CollectionBasedSource,
    CollectionRequiredError,
    EngineFactory,
    MediaItemRef,
    ModeEngine,
    PoolTooSmallError,
    TooManyCollectionsError,
    parse_mode_config,
)
from gv_testkit.game import (
    FakeCandidatePort,
    FakeCollectionPort,
    builtin_registry,
    media_item,
    run_sync,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
FACTORY = EngineFactory(builtin_registry(), supported_sessions=ALL_SESSIONS)
C1, C2, C3, C4 = (UUID(int=8_000 + i) for i in range(4))


def _films(prefix: str, count: int) -> list[MediaItemRef]:
    return [media_item(f"{prefix}{i}", domain="film") for i in range(count)]


def _engine(seed_body: SeedBody, key: str = "film.lobby.collection", **params: Any) -> ModeEngine:
    body = seed_body(key)
    body["source"]["params"].update(params)
    return FACTORY.build(parse_mode_config(body))


def _draw(
    engine: ModeEngine,
    port: FakeCollectionPort,
    n: int,
    *,
    ids: tuple[UUID, ...] = (),
    collection_id: UUID | None = None,
    seed: bytes = b"seed",
    exclude: frozenset[UUID] = frozenset(),
) -> list[MediaItemRef]:
    ctx = engine.draw_context(
        seed=seed,
        candidates=FakeCandidatePort(),
        collections=port,
        collection_id=collection_id,
        collection_ids=ids,
        exclude_ids=exclude,
    )
    return [d.target for d in run_sync(engine.draw_rounds(ctx, n))]


def _source(item: MediaItemRef, port: FakeCollectionPort) -> UUID:
    return next(cid for cid, items in port.collections.items() if item in items)


def test_collections_alternate_without_shuffle(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, shuffle=False)
    a, b, c = _films("A", 5), _films("B", 2), _films("C", 3)
    port = FakeCollectionPort({C1: a, C2: b, C3: c})
    got = _draw(engine, port, 10, ids=(C1, C2, C3))
    assert got == [a[0], b[0], c[0], a[1], b[1], c[1], a[2], c[2], a[3], a[4]]
    # Каждый сборник запрошен со своим scope (свои наборы ассетов сборника, ADR 0029).
    # Первые три запроса - розыгрыш; ещё три - поиск общей персоны (answer.subject_mode шаблона).
    scopes = [q[2].scope for q in port.queries if q[2] is not None]
    assert scopes[:3] == [C1, C2, C3]
    assert set(scopes) == {C1, C2, C3}


def test_shared_items_are_not_repeated(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, shuffle=False)
    shared = _films("S", 3)
    a, b = [*shared, *_films("A", 3)], [*shared, *_films("B", 3)]
    port = FakeCollectionPort({C1: a, C2: b})
    got = _draw(engine, port, 9, ids=(C1, C2, C1))  # повтор сборника в списке не считается
    assert len({i.id for i in got}) == 9
    assert got[:2] == [a[0], b[3]]
    assert engine.config.source.type == "collection"
    with pytest.raises(PoolTooSmallError) as info:
        _draw(engine, port, 10, ids=(C1, C2))
    assert info.value.details == {"available": 9, "required": 10}


def test_shuffled_draw_is_deterministic_and_alternates(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    a, b = _films("A", 10), _films("B", 10)
    port = FakeCollectionPort({C1: a, C2: b})
    first = _draw(engine, port, 10, ids=(C1, C2), seed=b"s1")
    assert first == _draw(engine, port, 10, ids=(C1, C2), seed=b"s1")
    assert any(_draw(engine, port, 10, ids=(C1, C2), seed=bytes([i])) != first for i in range(5))
    assert [_source(i, port) for i in first] == [C1, C2] * 5


def test_lobby_memory_used_last(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    a, b = _films("A", 4), _films("B", 4)
    port = FakeCollectionPort({C1: a, C2: b})
    played = frozenset(i.id for i in (*a[:3], *b[:3]))
    got = _draw(engine, port, 5, ids=(C1, C2), exclude=played)
    assert {i.id for i in got[:2]} == {a[3].id, b[3].id}
    assert all(i.id in played for i in got[2:])


def test_max_collections_enforced(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    port = FakeCollectionPort({cid: _films(str(n), 5) for n, cid in enumerate((C1, C2, C3, C4))})
    with pytest.raises(TooManyCollectionsError) as info:
        _draw(engine, port, 5, ids=(C1, C2, C3, C4))
    assert info.value.code == "game.too_many_collections"
    assert info.value.details == {"given": 4, "max": 3}


def test_solo_template_accepts_single_collection_only(seed_body: SeedBody) -> None:
    """У соло-шаблона ``max_collections`` по умолчанию 1: два сборника — ошибка."""
    engine = _engine(seed_body, "film.collection.frame")
    port = FakeCollectionPort({C1: _films("A", 10), C2: _films("B", 10)})
    with pytest.raises(TooManyCollectionsError):
        _draw(engine, port, 5, ids=(C1, C2))
    # collection_ids из одного сборника — то же, что collection_id (регресс 3b).
    assert _draw(engine, port, 5, ids=(C1,)) == _draw(engine, port, 5, collection_id=C1)


def test_collection_ids_win_over_collection_id_and_empty_is_required(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, shuffle=False)
    a, b = _films("A", 5), _films("B", 5)
    port = FakeCollectionPort({C1: a, C2: b})
    assert _draw(engine, port, 5, ids=(C2,), collection_id=C1) == b
    with pytest.raises(CollectionRequiredError):
        _draw(engine, port, 5)


def test_pool_size_counts_distinct_items(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    shared = _films("S", 2)
    port = FakeCollectionPort({C1: [*shared, *_films("A", 3)], C2: [*shared, *_films("B", 4)]})
    ctx = engine.draw_context(
        seed=b"s", candidates=FakeCandidatePort(), collections=port, collection_ids=(C1, C2)
    )
    assert run_sync(engine.rounds_for(ctx, 5)) == 5
    with pytest.raises(PoolTooSmallError):
        run_sync(engine.rounds_for(ctx, 10))
    assert isinstance(engine.source, CollectionBasedSource)
    assert run_sync(engine.source.pool_size(ctx, engine.source_params)) == 9


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    sizes=st.lists(st.integers(min_value=0, max_value=8), min_size=2, max_size=3),
    shared=st.integers(min_value=0, max_value=3),
    seed=st.binary(min_size=1, max_size=8),
    shuffle=st.booleans(),
)
def test_multi_draw_properties(
    sizes: list[int], shared: int, seed: bytes, shuffle: bool, seed_body: SeedBody
) -> None:
    engine = _engine(seed_body, shuffle=shuffle, min_items=1)
    common = _films("S", shared)
    ids = (C1, C2, C3)[: len(sizes)]
    port = FakeCollectionPort(
        {cid: [*common, *_films(str(cid.int), size)] for cid, size in zip(ids, sizes, strict=True)}
    )
    total = shared + sum(sizes)
    if total < 1:
        return
    n = min(total, 12)
    got = _draw(engine, port, n, ids=ids, seed=seed)
    assert len(got) == n
    assert len({i.id for i in got}) == n
    assert got == _draw(engine, port, n, ids=ids, seed=seed)
