"""Источники ``daily``, ``random``, ``collection``: пул, пресеты, детерминизм розыгрыша."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from gv_game_kernel import (
    DrawContext,
    MediaItemRef,
    PlayableFor,
    PoolQuery,
    PoolTooSmallError,
    Preset,
    PresetNotFoundError,
    daily_seed,
)
from gv_game_kernel.builtin import (
    CollectionParams,
    CollectionSource,
    DailyParams,
    DailySource,
    RandomParams,
    RandomSource,
)
from gv_testkit.game import FakeCandidatePort, FakeCollectionPort, media_item

pytestmark = pytest.mark.unit

HASH = "a" * 64
DAILY = DailyParams.model_validate(
    {
        "size": 3,
        "timezone": "Europe/Moscow",
        "schedule_ahead_days": 2,
        "exclude_recent_days": 7,
        "pool": {"kinds": ["track"], "min_playable": 5},
    }
)
RANDOM = RandomParams.model_validate(
    {
        "pool": {"kinds": ["track"], "min_popularity": 0.2},
        "presets": [
            {
                "id": "y2000s",
                "title_i18n": "p.y2000s",
                "filters": {"years": {"from": 2000, "to": 2009}},
            },
            {"id": "rock", "title_i18n": "p.rock", "filters": {"genres_any": ["rock"]}},
        ],
    }
)


def _pool(count: int = 10, **kwargs: Any) -> list[MediaItemRef]:
    return [media_item(f"T{i}", year=1995 + i, **kwargs) for i in range(count)]


def _ctx(port: Any, *, seed: bytes = b"seed", **kwargs: Any) -> DrawContext:
    return DrawContext(
        mode_key="music.daily.classic",
        domain="music",
        clue_type="audio_intro",
        params_hash=HASH,
        seed=seed,
        candidates=port,
        **kwargs,
    )


# ----------------------------------------------------------------------------- daily
async def test_daily_query_uses_pool_and_playable() -> None:
    port = FakeCandidatePort(_pool())
    picked = await DailySource().draw(_ctx(port), DAILY, 3)
    assert len({i.id for i in picked}) == 3
    query, limit = port.queries[0]
    assert query == PoolQuery(
        domain="music",
        kinds=frozenset({"track"}),
        playable_for=PlayableFor("audio_intro", HASH),
    )
    assert limit == 5000


async def test_daily_is_deterministic_by_seed_and_ignores_port_order() -> None:
    items = _pool(20)
    a = await DailySource().draw(_ctx(FakeCandidatePort(items)), DAILY, 5)
    b = await DailySource().draw(_ctx(FakeCandidatePort(list(reversed(items)))), DAILY, 5)
    c = await DailySource().draw(_ctx(FakeCandidatePort(items), seed=b"other"), DAILY, 5)
    assert a == b
    assert a != c


async def test_daily_excludes_recent_items_when_possible() -> None:
    items = _pool(6)
    recent = frozenset(i.id for i in items[:3])
    port = FakeCandidatePort(items)
    picked = await DailySource().draw(_ctx(port, exclude_ids=recent), DAILY, 3)
    assert {i.id for i in picked} == {i.id for i in items[3:]}
    assert port.queries[0][0].exclude_ids == recent
    assert len(port.queries) == 1


async def test_daily_falls_back_to_recent_items_when_fresh_are_short() -> None:
    items = _pool(4)
    recent = frozenset(i.id for i in items[:3])
    port = FakeCandidatePort(items)
    picked = await DailySource().draw(_ctx(port, exclude_ids=recent), DAILY, 3)
    ids = [i.id for i in picked]
    assert ids[0] == items[3].id  # сначала все свежие
    assert len(set(ids)) == 3 and set(ids[1:]) <= recent
    assert port.queries[1][0].exclude_ids == frozenset()


async def test_daily_pool_too_small() -> None:
    with pytest.raises(PoolTooSmallError) as info:
        await DailySource().draw(_ctx(FakeCandidatePort(_pool(2))), DAILY, 3)
    assert info.value.details == {"available": 2, "required": 3}
    recent = frozenset(i.id for i in _pool(0))
    with pytest.raises(PoolTooSmallError):
        items = _pool(2)
        await DailySource().draw(
            _ctx(FakeCandidatePort(items), exclude_ids=frozenset({items[0].id}) | recent),
            DAILY,
            3,
        )


async def test_daily_respects_playable_and_limit() -> None:
    items = _pool(5)
    playable = [(i.id, "audio_intro", HASH) for i in items[:3]]
    port = FakeCandidatePort(items, playable=playable)
    picked = await DailySource().draw(_ctx(port), DAILY, 3)
    assert {i.id for i in picked} == {i.id for i in items[:3]}
    with pytest.raises(PoolTooSmallError):
        await DailySource().draw(_ctx(FakeCandidatePort(items), candidate_limit=2), DAILY, 3)


async def test_draw_requires_positive_n() -> None:
    with pytest.raises(ValueError, match="хотя бы одну"):
        await DailySource().draw(_ctx(FakeCandidatePort(_pool())), DAILY, 0)


def test_daily_seed_is_keyed_and_stable() -> None:
    day = date(2026, 10, 10)
    seed = daily_seed(b"secret", "music.daily.classic", day)
    assert seed == daily_seed(b"secret", "music.daily.classic", day)
    assert seed != daily_seed(b"other", "music.daily.classic", day)
    assert seed != daily_seed(b"secret", "film.daily.frame", day)
    assert seed != daily_seed(b"secret", "music.daily.classic", day + timedelta(days=1))
    assert len(seed) == 32
    with pytest.raises(ValueError, match="секрет"):
        daily_seed(b"", "music.daily.classic", day)


@pytest.mark.parametrize(
    ("at", "day"),
    [
        (datetime(2026, 10, 9, 20, 59, tzinfo=UTC), date(2026, 10, 9)),
        (datetime(2026, 10, 9, 21, 0, tzinfo=UTC), date(2026, 10, 10)),  # 00:00 MSK
        (datetime(2026, 10, 10, 2, 0, tzinfo=timezone(timedelta(hours=5))), date(2026, 10, 10)),
    ],
)
def test_day_changes_at_midnight_in_mode_timezone(at: datetime, day: date) -> None:
    assert DailySource.day_of(DAILY, at) == day


def test_day_of_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="часовым поясом"):
        DailySource.day_of(DAILY, datetime(2026, 10, 10, 12, 0))  # noqa: DTZ001 — проверяем отказ


def test_daily_rejects_unknown_timezone() -> None:
    body = DAILY.model_dump(by_alias=True) | {"timezone": "Mars/Olympus"}
    with pytest.raises(ValidationError) as info:
        DailyParams.model_validate(body)
    assert info.value.errors()[0]["type"] == "timezone_unknown"


def test_daily_pool_query_and_min_playable() -> None:
    src = DailySource()
    assert src.presets(DAILY) == []
    assert src.min_playable(DAILY, rounds=3) == 5
    assert src.min_playable(DAILY, rounds=8) == 8
    query = src.pool_query(DAILY, domain="music", playable_for=None)
    assert query.kinds == frozenset({"track"})
    with pytest.raises(PresetNotFoundError):
        src.pool_query(DAILY, domain="music", playable_for=None, preset_id="rock")


# ----------------------------------------------------------------------------- random
async def test_random_applies_preset_filters() -> None:
    rock = [media_item(f"R{i}", genres=["rock"], year=1980) for i in range(3)]
    pop = [media_item(f"P{i}", genres=["pop"], year=2005) for i in range(3)]
    port = FakeCandidatePort([*rock, *pop])
    picked = await RandomSource().draw(_ctx(port, preset_id="rock"), RANDOM, 3)
    assert {i.id for i in picked} == {i.id for i in rock}
    picked = await RandomSource().draw(_ctx(port, preset_id="y2000s"), RANDOM, 3)
    assert {i.id for i in picked} == {i.id for i in pop}
    query = port.queries[-1][0]
    assert (query.year_from, query.year_to, query.min_popularity) == (2000, 2009, 0.2)


async def test_random_without_preset_uses_whole_pool_and_unknown_preset_fails() -> None:
    port = FakeCandidatePort(_pool(4))
    assert len(await RandomSource().draw(_ctx(port), RANDOM, 4)) == 4
    with pytest.raises(PresetNotFoundError) as info:
        await RandomSource().draw(_ctx(port, preset_id="jazz"), RANDOM, 1)
    assert info.value.code == "game.preset_not_found"


async def test_random_excludes_ids() -> None:
    items = _pool(3)
    port = FakeCandidatePort(items)
    with pytest.raises(PoolTooSmallError):
        await RandomSource().draw(_ctx(port, exclude_ids=frozenset({items[0].id})), RANDOM, 3)


def test_random_presets() -> None:
    assert RandomSource().presets(RANDOM) == [
        Preset("y2000s", "p.y2000s"),
        Preset("rock", "p.rock"),
    ]
    assert RandomSource().min_playable(RANDOM, rounds=10) == 10


def test_pool_narrowing_intersects_sets_and_ranges() -> None:
    params = RandomParams.model_validate(
        {
            "pool": {"genres_any": ["rock", "pop"], "years": {"from": 1990}, "min_popularity": 0.5},
            "presets": [
                {
                    "id": "p",
                    "title_i18n": "p.p",
                    "filters": {
                        "genres_any": ["rock", "jazz"],
                        "years": {"to": 1999},
                        "min_popularity": 0.1,
                        "kinds": ["track"],
                    },
                }
            ],
        }
    )
    query = RandomSource().pool_query(params, domain="music", playable_for=None, preset_id="p")
    assert query == PoolQuery(
        domain="music",
        kinds=frozenset({"track"}),
        genres_any=frozenset({"rock"}),
        year_from=1990,
        year_to=1999,
        min_popularity=0.5,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"pool": {"years": {"from": 2010, "to": 2000}}},
        {"pool": {"kinds": []}},
        {"pool": {"kinds": ["track", "track"]}},
        {"pool": {"genres_any": ["rock", "rock"]}},
        {"pool": {"genres_any": [""]}},
        {"pool": {"min_playable": 0}},
        {"pool": {}, "presets": [{"id": "Bad", "title_i18n": "a.b", "filters": {}}]},
        {"pool": {}, "presets": [{"id": "ok", "title_i18n": "ab", "filters": {}}]},
        {
            "pool": {},
            "presets": [{"id": "ok", "title_i18n": "a.b", "filters": {"min_playable": 1}}],
        },
    ],
)
def test_random_params_are_strict(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RandomParams.model_validate(body)


# ----------------------------------------------------------------------------- collection
async def test_collection_draw() -> None:
    cid = UUID(int=42)
    items = _pool(5)
    port = FakeCollectionPort({cid: [*items, items[0]]})
    shuffled = CollectionParams(collection_id=cid)
    ordered = CollectionParams(collection_id=cid, shuffle=False)
    src = CollectionSource()
    ctx = _ctx(FakeCandidatePort(), collections=port)
    assert await src.draw(ctx, ordered, 2) == items[:2]
    picked = await src.draw(ctx, shuffled, 5)
    assert sorted(i.id for i in picked) == sorted(i.id for i in items)
    assert picked == await src.draw(ctx, shuffled, 5)
    with pytest.raises(PoolTooSmallError):
        await src.draw(ctx, shuffled, 6)
    with pytest.raises(ValueError, match="CollectionPort"):
        await src.draw(_ctx(FakeCandidatePort()), shuffled, 1)
    assert src.presets(shuffled) == []
    assert src.pool_query(shuffled, domain="music", playable_for=None) is None
    assert src.min_playable(shuffled, rounds=7) == 7


# ----------------------------------------------------------------------------- свойства
@settings(max_examples=50)
@given(
    size=st.integers(1, 30),
    n=st.integers(1, 10),
    seed=st.binary(min_size=1, max_size=16),
    order=st.randoms(use_true_random=False),
)
async def test_draw_is_deterministic_for_same_seed_and_pool(
    size: int, n: int, seed: bytes, order: Any
) -> None:
    items = _pool(size)
    shuffled = list(items)
    order.shuffle(shuffled)
    src = RandomSource()
    if n > size:
        with pytest.raises(PoolTooSmallError):
            await src.draw(_ctx(FakeCandidatePort(items), seed=seed), RANDOM, n)
        return
    a = await src.draw(_ctx(FakeCandidatePort(items), seed=seed), RANDOM, n)
    b = await src.draw(_ctx(FakeCandidatePort(shuffled), seed=seed), RANDOM, n)
    assert a == b
    assert len({i.id for i in a}) == n
