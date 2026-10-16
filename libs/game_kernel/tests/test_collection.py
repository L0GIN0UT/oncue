"""Сессия по сборнику (этап 3b, ADR 0028): источник ``collection``, правила ``validate``,
число раундов и ``max_score`` движка, ``FakeCollectionPort`` и DSL ``given_collection``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from gv_game_kernel import (
    CollectionRequiredError,
    ConfigIssue,
    EngineFactory,
    MediaItemRef,
    ModeEngine,
    PlayableFor,
    PoolTooSmallError,
    RoundOption,
    RoundsNotAllowedError,
    parse_mode_config,
)
from gv_testkit.game import (
    FakeCandidatePort,
    FakeCollectionPort,
    build_engine,
    builtin_registry,
    given_mode,
    media_item,
    run_sync,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
KEY = "film.collection.frame"
CID = UUID(int=7_000)
FACTORY = EngineFactory(builtin_registry())
TEMPLATE: dict[str, Any] = yaml.safe_load(
    (Path(__file__).resolve().parent / "modes" / f"{KEY}.yaml").read_text(encoding="utf-8")
)


def _films(count: int) -> list[MediaItemRef]:
    return [media_item(f"F{i}", domain="film") for i in range(count)]


def _engine(seed_body: SeedBody, **params: Any) -> ModeEngine:
    body = seed_body(KEY)
    body["source"]["params"].update(params)
    return build_engine(body)


def _ctx(
    engine: ModeEngine,
    port: FakeCollectionPort | None,
    *,
    seed: bytes = b"seed",
    collection_id: UUID | None = CID,
    exclude_ids: frozenset[UUID] = frozenset(),
) -> Any:
    return engine.draw_context(
        seed=seed,
        candidates=FakeCandidatePort(),
        collections=port,
        collection_id=collection_id,
        exclude_ids=exclude_ids,
    )


def _ids(items: list[MediaItemRef]) -> list[UUID]:
    return [i.id for i in items]


# ----------------------------------------------------------------------------- A1 источник
async def test_draw_is_deterministic_by_seed(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    port = FakeCollectionPort({CID: _films(30)})
    a = await engine.draw(_ctx(engine, port), 10)
    b = await engine.draw(_ctx(engine, port), 10)
    others = [await engine.draw(_ctx(engine, port, seed=bytes([i])), 10) for i in range(5)]
    assert a == b
    assert len(set(_ids(a))) == 10
    assert any(o != a for o in others)


async def test_draw_without_shuffle_keeps_collection_order(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, shuffle=False)
    items = list(reversed(_films(12)))  # порядок автора не совпадает с порядком id
    port = FakeCollectionPort({CID: items})
    assert await engine.draw(_ctx(engine, port), 5) == items[:5]
    assert await engine.draw(_ctx(engine, port, seed=b"other"), 5) == items[:5]


async def test_draw_deduplicates_repeats(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, shuffle=False)
    items = _films(5)
    port = FakeCollectionPort({CID: [items[0], *items, items[2]]})
    assert await engine.draw(_ctx(engine, port), 5) == items
    with pytest.raises(PoolTooSmallError):
        await engine.draw(_ctx(engine, port), 6)


async def test_draw_avoids_excluded_items_while_possible(seed_body: SeedBody) -> None:
    items = _films(8)
    port = FakeCollectionPort({CID: items})
    recent = frozenset(_ids(items[:3]))
    shuffled = _engine(seed_body)
    picked = await shuffled.draw(_ctx(shuffled, port, exclude_ids=recent), 5)
    assert set(_ids(picked)) == set(_ids(items[3:]))
    filled = await shuffled.draw(_ctx(shuffled, port, exclude_ids=recent), 7)
    assert set(_ids(filled[:5])) == set(_ids(items[3:]))  # сначала все свежие
    assert set(_ids(filled[5:])) <= recent
    ordered = _engine(seed_body, shuffle=False)
    kept = await ordered.draw(_ctx(ordered, port, exclude_ids=recent), 6)
    assert kept == [items[0], *items[3:]]  # добор из недавних, порядок сборника


async def test_draw_requires_collection_and_port(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    port = FakeCollectionPort({CID: _films(10)})
    with pytest.raises(CollectionRequiredError) as exc:
        await engine.draw(_ctx(engine, port, collection_id=None), 5)
    assert exc.value.code == "game.collection_required"
    with pytest.raises(ValueError, match="CollectionPort"):
        await engine.draw(_ctx(engine, None), 5)
    with pytest.raises(CollectionRequiredError):
        await engine.rounds_for(_ctx(engine, port, collection_id=None))


async def test_draw_rejects_pool_below_min_items(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, min_items=6)
    port = FakeCollectionPort({CID: _films(5)})
    with pytest.raises(PoolTooSmallError) as exc:
        await engine.draw(_ctx(engine, port), 1)
    assert exc.value.details == {"available": 5, "required": 6}
    with pytest.raises(PoolTooSmallError):
        await engine.rounds_for(_ctx(engine, port), "all")
    with pytest.raises(PoolTooSmallError):
        await engine.draw(_ctx(engine, FakeCollectionPort()), 1)  # неизвестный сборник


async def test_draw_asks_port_for_scoped_playability(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, min_items=4)
    items = _films(10)
    h = engine.clue_params_hash
    port = FakeCollectionPort(
        {CID: items},
        playable=[(i.id, "frame_progressive", h) for i in items[:4]],
        scoped=[(CID, items[9].id, "frame_progressive", h)],
    )
    port.block(items[0].id)
    picked = await engine.draw(_ctx(engine, port), 4)
    assert set(_ids(picked)) == {items[1].id, items[2].id, items[3].id, items[9].id}
    collection_id, limit, playable_for = port.queries[0]
    assert (collection_id, limit) == (CID, 5000)
    assert playable_for == PlayableFor("frame_progressive", h, scope=CID)


# ----------------------------------------------------------------------------- A3 раунды
@pytest.mark.parametrize(
    ("requested", "pool", "rounds"),
    [(None, 30, 5), (10, 30, 10), (25, 25, 25), ("all", 7, 7), ("all", 60, 60)],
)
async def test_rounds_for_follows_choice_and_pool(
    seed_body: SeedBody, requested: RoundOption | None, pool: int, rounds: int
) -> None:
    engine = _engine(seed_body)
    port = FakeCollectionPort({CID: _films(pool)})
    ctx = _ctx(engine, port)
    n = await engine.rounds_for(ctx, requested)
    assert n == rounds
    draws = await engine.draw_rounds(ctx, n)
    assert len(draws) == rounds
    assert engine.max_score(len(draws)) == 6 * rounds


async def test_rounds_for_caps_all_by_session_rounds(seed_body: SeedBody) -> None:
    body = seed_body(KEY)
    body["source"]["params"]["round_options"] = [5, "all"]
    body["session"]["rounds"] = 8
    engine = build_engine(body)
    ctx = _ctx(engine, FakeCollectionPort({CID: _films(20)}))
    assert await engine.rounds_for(ctx, "all") == 8


async def test_rounds_for_rejects_unknown_option(seed_body: SeedBody) -> None:
    engine = _engine(seed_body, round_options=[5, 25])
    ctx = _ctx(engine, FakeCollectionPort({CID: _films(30)}))
    with pytest.raises(RoundsNotAllowedError) as exc:
        await engine.rounds_for(ctx, "all")
    assert exc.value.code == "game.rounds_not_allowed"
    assert exc.value.details == {"requested": "all", "options": [5, 25]}
    with pytest.raises(PoolTooSmallError):
        await _engine(seed_body).rounds_for(_ctx(engine, FakeCollectionPort({CID: _films(9)})), 10)


async def test_engine_exposes_collection_capabilities(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    assert engine.uses_collection
    assert engine.round_options() == [5, 10, 25, "all"]
    assert engine.presets() == []
    assert engine.pool_query() is None
    assert engine.min_playable() == 5
    random = build_engine(seed_body("film.random.frame"))
    assert not random.uses_collection
    assert random.round_options() is None
    ctx = random.draw_context(seed=b"s", candidates=FakeCandidatePort())
    assert await random.rounds_for(ctx) == random.planned_rounds()
    with pytest.raises(RoundsNotAllowedError) as exc:
        await random.rounds_for(ctx, 5)
    assert exc.value.details == {"requested": 5, "options": []}
    with pytest.raises(ValueError, match="другого режима"):
        await random.rounds_for(_ctx(engine, FakeCollectionPort()))


@settings(max_examples=40, deadline=None)
@given(
    pool=st.integers(min_value=5, max_value=60),
    requested=st.sampled_from([None, 5, 10, 25, "all"]),
    voids=st.integers(min_value=0, max_value=5),
    seed=st.binary(min_size=1, max_size=8),
)
def test_max_score_is_sum_over_issued_rounds(
    pool: int, requested: RoundOption | None, voids: int, seed: bytes
) -> None:
    body = parse_mode_config(TEMPLATE)
    engine = FACTORY.build(body)
    items = _films(pool)
    expected = pool if requested == "all" else (requested or 5)
    if expected > pool:
        with pytest.raises(PoolTooSmallError):
            given_mode(engine).given_collection(items, rounds=requested, seed=seed)
        return
    scenario = given_mode(engine).given_collection(items, rounds=requested, seed=seed)
    scenario.then_rounds(expected)
    assert {r.item.id for r in scenario.rounds} <= {i.id for i in items}
    for _ in range(min(voids, expected)):
        scenario.when_void()
    issued = [r for r in scenario.rounds if str(r.state.outcome) != "void"]
    assert scenario.max_score() == sum(engine.round_max_score() for _ in issued)
    assert scenario.max_score() == engine.max_score(expected - min(voids, expected))


# ----------------------------------------------------------------------------- A2 validate
def _issues(body: dict[str, Any]) -> list[ConfigIssue]:
    return FACTORY.validate(parse_mode_config(body))


def test_collection_template_is_valid(seed_body: SeedBody) -> None:
    assert _issues(seed_body(KEY)) == []
    body = seed_body(KEY)
    body["features"]["records"] = False
    assert _issues(body) == []


def test_collection_allows_only_classic(seed_body: SeedBody) -> None:
    body = seed_body(KEY)
    body["answer"]["format"] = "pick"
    assert ConfigIssue("source.type", "config.answer_format_unsupported") in _issues(body)


def test_collection_round_options_fit_session_rounds(seed_body: SeedBody) -> None:
    body = seed_body(KEY)
    body["session"]["rounds"] = 10
    assert _issues(body) == [
        ConfigIssue("source.params.round_options[2]", "config.round_option_above_rounds")
    ]
    body["source"]["params"]["round_options"] = [5, 10, "all"]
    assert _issues(body) == []


def test_collection_forbids_records(seed_body: SeedBody) -> None:
    body = seed_body(KEY)
    body["features"]["records"] = True
    assert _issues(body) == [ConfigIssue("features.records", "config.records_unsupported")]
    random = seed_body("film.random.frame")
    random["features"]["records"] = True
    assert _issues(random) == []


# ----------------------------------------------------------------------------- A4 testkit
async def test_fake_collection_port_semantics() -> None:
    items = _films(4)
    pf = PlayableFor("emoji", "h")
    port = FakeCollectionPort(playable=[(items[1].id, "emoji", "h")])
    port.add(CID, items[3], items[0], items[1])
    port.add(CID, items[2])
    assert await port.items(CID, 10, playable_for=None) == [items[3], items[0], items[1], items[2]]
    assert await port.items(CID, 2, playable_for=None) == [items[3], items[0]]
    assert await port.items(CID, 10, playable_for=pf) == [items[1]]
    own = [(CID, items[2].id, "emoji", "h")]
    scoped = FakeCollectionPort({CID: items}, playable=[], scoped=own)
    assert await scoped.items(CID, 10, playable_for=pf) == []  # без scope — только каталожные
    in_scope = PlayableFor("emoji", "h", scope=CID)
    assert await scoped.items(CID, 10, playable_for=in_scope) == [items[2]]
    other_scope = PlayableFor("emoji", "h", scope=UUID(int=1))
    assert await scoped.items(CID, 10, playable_for=other_scope) == []
    scoped.block(items[2].id)
    assert await scoped.items(CID, 10, playable_for=in_scope) == []
    port.remove(CID)
    assert await port.items(CID, 10, playable_for=None) == []
    assert await port.items(UUID(int=99), 10, playable_for=None) == []


def test_given_collection_plays_like_game(seed_body: SeedBody) -> None:
    items = _films(12)
    scenario = given_mode(_engine(seed_body, shuffle=False)).given_collection(items, rounds=10)
    scenario.then_rounds(10)
    assert scenario.collection_id is not None
    assert scenario.collections is not None
    assert [r.item for r in scenario.rounds] == items[:10]
    scenario.when_guess(item=items[0]).then_round(outcome="exact", level=1, points=6)
    port = FakeCollectionPort({CID: items})
    again = given_mode(_engine(seed_body)).given_collection(
        rounds="all", port=port, collection_id=CID, seed=b"x"
    )
    again.then_rounds(12)
    with pytest.raises(RoundsNotAllowedError):
        given_mode(_engine(seed_body, round_options=[5])).given_collection(items, rounds=10)


def test_run_sync_rejects_real_io() -> None:
    async def waits() -> int:
        await asyncio.sleep(0)
        return 1

    with pytest.raises(RuntimeError, match="IO"):
        run_sync(waits())
