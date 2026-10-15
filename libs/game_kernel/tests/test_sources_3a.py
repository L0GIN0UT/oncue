"""Источники этапа 3a: марафон по персонам, цепочка «больше/меньше», недельная сетка."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gv_game_kernel import (
    PoolTooSmallError,
    PresetNotFoundError,
    RoundDraw,
    SubjectRequiredError,
    TooManySubjectsError,
    parse_mode_config,
)
from gv_game_kernel.builtin import BracketParams, BracketSource, PairParams, PairSource
from gv_game_kernel.builtin.sources import PAIR_OPTIONS, _chain
from gv_testkit.game import FakeCandidatePort, build_engine, given_mode, media_item, person

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]


# ----------------------------------------------------------------------------- marathon
@pytest.fixture
def artists() -> dict[str, Any]:
    a, b, c = person("Artist A"), person("Artist B"), person("Artist C")
    items = [media_item(f"A{i}", persons=[a], year=2000 + i) for i in range(6)]
    items += [media_item(f"B{i}", persons=[b], year=1990 + i) for i in range(4)]
    items += [media_item("AB", persons=[a, b], year=None)]
    items += [media_item(f"C{i}", persons=[c]) for i in range(5)]
    return {"a": a, "b": b, "c": c, "items": items}


async def test_marathon_draws_only_chosen_persons(
    seed_body: SeedBody, artists: dict[str, Any]
) -> None:
    engine = build_engine(seed_body("music.marathon.artist"))
    port = FakeCandidatePort(artists["items"])
    ctx = engine.draw_context(seed=b"s1", candidates=port, subject_ids=frozenset({artists["a"].id}))
    items = await engine.draw(ctx)
    # n = min(пул, rounds): у A 6 своих единиц + общая с B.
    assert len(items) == 7
    assert all(any(p.id == artists["a"].id for p in i.persons) for i in items)
    assert port.queries[0][0].person_ids_any == frozenset({artists["a"].id})
    both = engine.draw_context(
        seed=b"s1", candidates=port, subject_ids=frozenset({artists["a"].id, artists["b"].id})
    )
    assert len(await engine.draw(both)) == 11


async def test_marathon_rounds_cap_and_determinism(
    seed_body: SeedBody, artists: dict[str, Any]
) -> None:
    body = seed_body("music.marathon.artist")
    body["session"]["rounds"] = 3
    engine = build_engine(body)
    port = FakeCandidatePort(artists["items"])
    subjects = frozenset({artists["a"].id})
    ctx = engine.draw_context(seed=b"s1", candidates=port, subject_ids=subjects)
    first = await engine.draw_rounds(ctx)
    assert len(first) == 3 and first == await engine.draw_rounds(ctx)
    assert all(d.shown == () and d.options == () for d in first)
    others = [
        await engine.draw_rounds(
            engine.draw_context(seed=bytes([i]), candidates=port, subject_ids=subjects)
        )
        for i in range(5)
    ]
    assert any(o != first for o in others)


async def test_marathon_order_by_year(seed_body: SeedBody, artists: dict[str, Any]) -> None:
    body = seed_body("music.marathon.artist")
    body["source"]["params"]["order"] = "year"
    engine = build_engine(body)
    ctx = engine.draw_context(
        seed=b"x",
        candidates=FakeCandidatePort(artists["items"]),
        subject_ids=frozenset({artists["a"].id, artists["b"].id}),
    )
    years = [i.year for i in await engine.draw(ctx)]
    assert years[-1] is None
    known = [y for y in years if y is not None]
    assert known == sorted(known)


async def test_marathon_subject_errors(seed_body: SeedBody, artists: dict[str, Any]) -> None:
    engine = build_engine(seed_body("music.marathon.artist"))
    port = FakeCandidatePort(artists["items"])
    with pytest.raises(SubjectRequiredError) as missing:
        await engine.draw(engine.draw_context(seed=b"s", candidates=port))
    assert missing.value.code == "game.subject_required"
    many = frozenset(person(f"P{i}").id for i in range(4))
    with pytest.raises(TooManySubjectsError) as too_many:
        await engine.draw(engine.draw_context(seed=b"s", candidates=port, subject_ids=many))
    assert too_many.value.code == "game.too_many_subjects"
    assert too_many.value.details == {"given": 4, "max": 3}
    stranger = frozenset({person("Nobody").id})
    with pytest.raises(PoolTooSmallError) as empty:
        await engine.draw(engine.draw_context(seed=b"s", candidates=port, subject_ids=stranger))
    assert empty.value.details == {"available": 0, "required": 1}


def test_marathon_static_view(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("music.marathon.artist"))
    query = engine.subject_query()
    assert query is not None
    assert query.roles == frozenset({"artist"}) and query.min_items == 5
    assert query.playable_for == engine.playable_for
    assert engine.max_subjects() == 3
    assert engine.presets() == []
    assert engine.min_playable() == 5
    assert engine.pool_query() is not None
    with pytest.raises(PresetNotFoundError):
        engine.pool_query("rock")
    assert engine.followups([]) == [] and engine.round_stage(0) is None
    assert engine.planned_rounds() == 200
    assert engine.period_start(datetime(2026, 1, 1, tzinfo=UTC)) is None
    assert engine.theme_query() is None


def test_fake_subject_search(artists: dict[str, Any]) -> None:
    from gv_game_kernel import SubjectQuery

    port = FakeCandidatePort(artists["items"])
    hits = port.subjects(SubjectQuery(domain="music", roles=frozenset({"artist"}), min_items=5))
    expected = [("Artist A", 7), ("Artist B", 5), ("Artist C", 5)]
    assert [(h.person.name, h.items) for h in hits] == expected
    found = port.subjects(
        SubjectQuery(domain="music", roles=frozenset({"artist"}), min_items=1), text="artist b"
    )
    assert [h.person.name for h in found] == ["Artist B"]
    directors = SubjectQuery(domain="music", roles=frozenset({"director"}), min_items=1)
    assert port.subjects(directors) == []


# ----------------------------------------------------------------------------- pair
def _films(values: list[float | None]) -> list[Any]:
    return [
        media_item(f"F{i}", domain="film", metrics={} if v is None else {"vote_average": v})
        for i, v in enumerate(values)
    ]


async def test_pair_chain_neighbours_differ_by_gap(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.pair.rating"))
    values = [5.0, 5.0, 5.1, 5.2, 6.0, 6.0, 7.5, 8.0, 8.1, 3.3, None]
    ctx = engine.draw_context(seed=b"pair", candidates=FakeCandidatePort(_films(values)))
    draws = await engine.draw_rounds(ctx)
    assert draws
    for d in draws:
        anchor, target = d.shown[0].metric("vote_average"), d.target.metric("vote_average")
        assert anchor is not None and target is not None
        assert abs(anchor - target) >= 0.3 and anchor != target
        assert d.options == PAIR_OPTIONS
    # Цепочка: загаданная единица раунда — опорная в следующем.
    assert all(a.target == b.shown[0] for a, b in pairwise(draws))
    assert draws == await engine.draw_rounds(ctx)


async def test_pair_respects_rounds(seed_body: SeedBody) -> None:
    body = seed_body("film.pair.rating")
    body["session"]["rounds"] = 3
    engine = build_engine(body)
    ctx = engine.draw_context(
        seed=b"s", candidates=FakeCandidatePort(_films([float(i) for i in range(20)]))
    )
    assert len(await engine.draw_rounds(ctx)) == 3
    assert len(await engine.draw(ctx)) == 4


@pytest.mark.parametrize("values", [[7.0], [7.0, 7.0, 7.0], [7.0, 7.1], [None, None]])
async def test_pair_pool_too_small(seed_body: SeedBody, values: list[float | None]) -> None:
    engine = build_engine(seed_body("film.pair.rating"))
    ctx = engine.draw_context(seed=b"s", candidates=FakeCandidatePort(_films(values)))
    with pytest.raises(PoolTooSmallError) as info:
        await engine.draw_rounds(ctx)
    assert info.value.details["required"] == 2


@settings(max_examples=60, deadline=None)
@given(
    values=st.lists(st.integers(min_value=0, max_value=20).map(lambda x: x / 2), max_size=40),
    gap=st.sampled_from([0.0, 0.5, 1.0, 3.0]),
    limit=st.integers(min_value=1, max_value=50),
)
def test_chain_property(values: list[float], gap: float, limit: int) -> None:
    """У цепочки нет соседей с равной метрикой или ближе ``min_gap``; длина ≤ limit."""
    chain = _chain(_films(list(values)), "vote_average", gap, limit)
    assert len(chain) <= limit
    assert len({i.id for i in chain}) == len(chain)
    for a, b in pairwise(chain):
        x, y = a.metric("vote_average"), b.metric("vote_average")
        assert x is not None and y is not None
        assert x != y and abs(x - y) >= gap


def test_pair_static_view_and_cross_check(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.pair.rating"))
    assert engine.metric_name == "vote_average"
    assert engine.min_playable() == 10
    query = engine.pool_query()
    assert query is not None and query.has_metric == "vote_average"
    with pytest.raises(PresetNotFoundError):
        engine.pool_query("x")
    source, params = (
        PairSource(),
        PairParams.model_validate(
            {"metric": "revenue_usd", "min_gap": 0, "ties": "both_correct", "pool": {}}
        ),
    )
    music_body = {**seed_body("film.pair.rating"), "domain": "music", "key": "music.pair.revenue"}
    music = parse_mode_config(music_body)
    assert [i.code for i in source.cross_check(params, config=music, levels_count=1)] == [
        "config.metric_unsupported"
    ]
    assert source.presets(params) == []
    assert source.min_playable(params, rounds=50) == 2


# ----------------------------------------------------------------------------- bracket
def _bracket_body(seed_body: SeedBody, size: int) -> dict[str, Any]:
    body = seed_body("film.bracket.weekly")
    body["source"]["params"]["size"] = size
    body["source"]["params"]["subject"]["min_items"] = size
    body["session"]["rounds"] = size - 1
    return body


@pytest.mark.parametrize(
    ("size", "stages"),
    [(8, [8, 4, 2]), (16, [16, 8, 4, 2]), (32, [32, 16, 8, 4, 2])],
)
async def test_bracket_full_run(seed_body: SeedBody, size: int, stages: list[int]) -> None:
    """Сетка size → size−1 выборов, туры size/2 … 1; повторный розыгрыш — тот же посев."""
    engine = build_engine(_bracket_body(seed_body, size))
    director = person("Director", "director")
    items = [media_item(f"F{i}", domain="film", persons=[director]) for i in range(size + 5)]
    port = FakeCandidatePort(items)
    ctx = engine.draw_context(seed=b"week", candidates=port, subject_ids=frozenset({director.id}))
    first = await engine.draw_rounds(ctx)
    assert len(first) == size // 2 and first == await engine.draw_rounds(ctx)
    assert engine.planned_rounds() == size - 1 and engine.draw_size == size
    scenario = given_mode(engine).with_draws(*first)
    picks = 0
    while not scenario.session_ended:
        # Всегда выбираем второй вариант: победитель — правая единица пары.
        scenario.when_pick("b")
        picks += 1
    assert picks == size - 1 == len(scenario.rounds)
    tours = [engine.round_stage(i) or 0 for i in range(size - 1)]
    assert [tours.count(s) for s in stages] == [s // 2 for s in stages]
    assert sorted(set(tours), reverse=True) == stages
    final = scenario.rounds[-1]
    assert final.draw.options[1].item == first[-1].shown[1]
    scenario.then_session_ended().then_score(0)
    assert engine.followups([r.pick() for r in scenario.rounds]) == []


async def test_bracket_followups_wait_for_full_stage(seed_body: SeedBody) -> None:
    engine = build_engine(_bracket_body(seed_body, 8))
    items = [media_item(f"F{i}", domain="film") for i in range(8)]
    rounds = engine.compose_rounds(items)
    assert [(r.shown[0], r.shown[1]) for r in rounds] == [
        (items[i], items[i + 1]) for i in range(0, 8, 2)
    ]
    scenario = given_mode(engine).with_draws(*rounds)
    scenario.when_pick("a").when_pick("b").when_pick("a")
    assert len(scenario.rounds) == 4  # тур не доигран — новых пар нет
    scenario.when_void()  # аннулированный раунд: проходит первая единица пары
    assert len(scenario.rounds) == 6
    semis = [r.draw for r in scenario.rounds[4:]]
    assert [(d.shown[0], d.shown[1]) for d in semis] == [(items[0], items[3]), (items[4], items[6])]
    assert all(o.item is not None for d in semis for o in d.options)
    assert [o.id for o in semis[0].options] == ["a", "b"]


async def test_bracket_errors(seed_body: SeedBody) -> None:
    engine = build_engine(_bracket_body(seed_body, 8))
    port = FakeCandidatePort([media_item(f"F{i}", domain="film") for i in range(7)])
    ctx = engine.draw_context(seed=b"w", candidates=port)
    with pytest.raises(PoolTooSmallError) as info:
        await engine.draw_rounds(ctx)
    assert info.value.details == {"available": 7, "required": 8}
    with pytest.raises(ValueError, match="ровно 8"):
        await engine.draw(ctx, 4)
    with pytest.raises(ValueError, match="должно быть 8"):
        engine.compose_rounds([media_item("x", domain="film")])
    with pytest.raises(ValueError, match="нет раунда"):
        engine.round_stage(7)
    from gv_game_kernel import OptionSpec, RoundOutcome, RoundPick, RoundResult

    broken = RoundPick(
        draw=RoundDraw(target=media_item("x", domain="film"), options=(OptionSpec("a"),)),
        outcome=RoundOutcome(RoundResult.EXACT, level=1),
        option_id="a",
    )
    with pytest.raises(ValueError, match="без вариантов"):
        engine.followups([broken] * 4)


def test_bracket_period_and_theme(seed_body: SeedBody) -> None:
    engine = build_engine(_bracket_body(seed_body, 32))
    # 2026-10-18 — воскресенье; 21:30 UTC = 00:30 понедельника 19-го в Москве.
    assert engine.period_start(datetime(2026, 10, 18, 12, 0, tzinfo=UTC)) == date(2026, 10, 12)
    assert engine.period_start(datetime(2026, 10, 18, 21, 30, tzinfo=UTC)) == date(2026, 10, 19)
    with pytest.raises(ValueError, match="часовым поясом"):
        engine.period_start(datetime(2026, 10, 18, 12, 0))  # noqa: DTZ001 — проверка отказа
    theme = engine.theme_query()
    assert theme is not None and theme.roles == frozenset({"director"}) and theme.min_items == 32
    assert engine.subject_query() is None and engine.max_subjects() is None
    assert engine.min_playable() == 32 and engine.presets() == []
    query = engine.pool_query()
    assert query is not None and query.person_ids_any == frozenset()
    with pytest.raises(PresetNotFoundError):
        engine.pool_query("x")


@pytest.mark.parametrize(
    ("patch", "code", "path"),
    [
        ({"rounds": 30}, "config.rounds_mismatch", "size"),
        ({"min_items": 16}, "config.subject_min_items_below_size", "subject.min_items"),
        ({"min_playable": 20}, "config.min_playable_below_rounds", "pool.min_playable"),
    ],
)
def test_bracket_cross_check(
    seed_body: SeedBody, patch: dict[str, int], code: str, path: str
) -> None:
    body = _bracket_body(seed_body, 32)
    if "rounds" in patch:
        body["session"]["rounds"] = patch["rounds"]
    if "min_items" in patch:
        body["source"]["params"]["subject"]["min_items"] = patch["min_items"]
    if "min_playable" in patch:
        body["source"]["params"]["pool"]["min_playable"] = patch["min_playable"]
    config = parse_mode_config(body)
    params = BracketParams.model_validate(body["source"]["params"])
    issues = BracketSource().cross_check(params, config=config, levels_count=1)
    assert [(i.path, i.code) for i in issues] == [(path, code)]


def test_daily_is_period_source(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("music.daily.classic"))
    moment = datetime(2026, 10, 18, 21, 30, tzinfo=UTC)
    assert engine.period_start(moment) == date(2026, 10, 19)  # Europe/Moscow
    assert engine.theme_query() is None
