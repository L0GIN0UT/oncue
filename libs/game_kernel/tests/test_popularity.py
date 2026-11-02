"""Популярность в источнике единиц (ADR 0074): порог по процентилю, веса, дейлик и «Хиты»."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from gv_core.random import SeededRandom
from gv_game_kernel import DrawContext, MediaItemRef, PoolQuery, PoolTooSmallError
from gv_game_kernel.builtin import DailyParams, DailySource, RandomParams, RandomSource
from gv_game_kernel.builtin.pool import PoolFilters, PoolParams, build_query
from gv_game_kernel.builtin.sources import (
    MIN_POPULARITY_WEIGHT,
    _sample,
    popularity_weights,
)
from gv_testkit.game import FakeCandidatePort, media_item

pytestmark = pytest.mark.unit

HASH = "a" * 64


def _tracks(count: int, *, scale: float = 1.0) -> list[MediaItemRef]:
    """Треки с популярностью 1..count (умноженной на ``scale``); T{i} - i-я по популярности."""
    return [media_item(f"T{i}", popularity=(i + 1) * scale) for i in range(count)]


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


def _daily(pct: float | None, weight: str = "none", size: int = 3) -> DailyParams:
    pool: dict[str, Any] = {"kinds": ["track"], "min_playable": size, "popularity_weight": weight}
    if pct is not None:
        pool["popularity_top_pct"] = pct
    return DailyParams.model_validate(
        {
            "size": size,
            "timezone": "Europe/Moscow",
            "schedule_ahead_days": 2,
            "exclude_recent_days": 7,
            "pool": pool,
        }
    )


# ----------------------------------------------------------------------------- схема
@pytest.mark.parametrize("pct", [0, 0.5, 101, 1000])
def test_top_pct_out_of_range_is_rejected(pct: float) -> None:
    with pytest.raises(ValidationError):
        PoolFilters.model_validate({"popularity_top_pct": pct})


def test_top_pct_and_weight_accept_valid_values() -> None:
    pool = PoolParams.model_validate({"popularity_top_pct": 30, "popularity_weight": "log"})
    assert (pool.popularity_top_pct, pool.popularity_weight) == (30.0, "log")
    assert PoolParams.model_validate({}).popularity_weight == "none"
    with pytest.raises(ValidationError):
        PoolParams.model_validate({"popularity_weight": "cube"})


def test_preset_and_pool_percentages_narrow_to_the_smaller() -> None:
    pool = PoolParams.model_validate({"kinds": ["track"], "popularity_top_pct": 30})
    wider = PoolFilters.model_validate({"popularity_top_pct": 50})
    hits = PoolFilters.model_validate({"popularity_top_pct": 20})
    by_wider = build_query(pool, domain="music", playable_for=None, extra=wider)
    by_hits = build_query(pool, domain="music", playable_for=None, extra=hits)
    assert by_wider.popularity_top_pct == 30
    assert by_hits.popularity_top_pct == 20
    plain = PoolParams.model_validate({"kinds": ["track"]})
    assert build_query(plain, domain="music", playable_for=None).popularity_top_pct is None


# ----------------------------------------------------------------------------- веса
def test_uniform_mode_has_no_weights() -> None:
    assert popularity_weights(_tracks(5), "none") is None


@pytest.mark.parametrize("mode", ["sqrt", "log"])
def test_weights_grow_with_rank_and_ignore_scale(mode: Any) -> None:
    small, huge = _tracks(10), _tracks(10, scale=1e5)
    a = popularity_weights(small, mode)
    b = popularity_weights(huge, mode)
    assert a is not None and b is not None
    # Ранг, а не сама метрика: фильмы (10-500) и треки (до 1e6) взвешиваются одинаково.
    assert [a[i.id] for i in small] == pytest.approx([b[i.id] for i in huge])
    ordered = [a[i.id] for i in small]
    assert ordered == sorted(ordered) and ordered[0] < ordered[-1] <= 1.0
    assert min(ordered) >= MIN_POPULARITY_WEIGHT


def test_missing_popularity_gets_floor_weight_and_ties_share_rank() -> None:
    unknown = media_item("U", popularity=None)
    twins = [media_item("A", popularity=5.0), media_item("B", popularity=5.0)]
    top = media_item("Top", popularity=9.0)
    weights = popularity_weights([unknown, *twins, top], "sqrt")
    assert weights is not None
    assert weights[unknown.id] == MIN_POPULARITY_WEIGHT
    assert weights[twins[0].id] == weights[twins[1].id] < weights[top.id]


def test_weighted_sample_is_deterministic_distinct_and_favours_popular() -> None:
    items = _tracks(40)
    first = _sample(items, 5, SeededRandom(b"s"), "sqrt")
    again = _sample(list(reversed(items)), 5, SeededRandom(b"s"), "sqrt")
    assert first == again and len({i.id for i in first}) == 5
    top = {i.id for i in items[30:]}
    bottom = {i.id for i in items[:10]}
    hits_top = hits_bottom = 0
    for n in range(300):
        picked = _sample(items, 5, SeededRandom(f"seed-{n}".encode()), "sqrt")
        hits_top += sum(i.id in top for i in picked)
        hits_bottom += sum(i.id in bottom for i in picked)
    # Популярные чаще, но малоизвестные не исчезают.
    assert hits_top > 2 * hits_bottom > 0


def test_uniform_sample_is_unchanged_by_the_weight_parameter() -> None:
    items = _tracks(20)
    assert _sample(items, 5, SeededRandom(b"s")) == _sample(items, 5, SeededRandom(b"s"), "none")


# ----------------------------------------------------------------------------- дейлик
async def test_daily_draws_only_from_the_top_percent() -> None:
    items = _tracks(40)
    top_ids = {i.id for i in items[28:]}  # верхние 30% - 12 единиц
    port = FakeCandidatePort(items)
    for n in range(20):
        picked = await DailySource().draw(
            _ctx(port, seed=f"d{n}".encode()), _daily(30, "sqrt", size=5), 5
        )
        assert {i.id for i in picked} <= top_ids
    assert port.queries[0][0].popularity_top_pct == 30


async def test_daily_pool_after_truncation_is_the_minimum_size() -> None:
    """Пул 30% от 40 - 12 единиц: дейлик на 5 раундов собирается, а при 3 единицах - нет."""
    params = _daily(30, size=5)
    assert len(await DailySource().draw(_ctx(FakeCandidatePort(_tracks(40))), params, 5)) == 5
    with pytest.raises(PoolTooSmallError) as info:
        await DailySource().draw(_ctx(FakeCandidatePort(_tracks(10))), params, 5)
    assert info.value.details == {"available": 3, "required": 5}


async def test_daily_keeps_exclude_recent_over_the_truncated_pool() -> None:
    items = _tracks(40)
    top = items[28:]
    recent = frozenset(i.id for i in top[:10])  # недавние: 10 из 12 верхних
    port = FakeCandidatePort(items)
    picked = await DailySource().draw(_ctx(port, exclude_ids=recent), _daily(30, size=3), 3)
    ids = [i.id for i in picked]
    # Свежих только 2: берутся оба, третья добирается из недавних того же усечённого пула.
    assert set(ids[:2]) == {i.id for i in top[10:]}
    assert ids[2] in recent
    assert {i.id for i in picked} <= {i.id for i in top}


# ----------------------------------------------------------------------------- «Хиты»
async def test_hits_preset_narrows_a_random_mode_to_the_top_twenty_percent() -> None:
    params = RandomParams.model_validate(
        {
            "pool": {"kinds": ["track"]},
            "presets": [
                {"id": "hits", "title_i18n": "p.hits", "filters": {"popularity_top_pct": 20}}
            ],
        }
    )
    items = _tracks(50)
    picked = await RandomSource().draw(_ctx(FakeCandidatePort(items), preset_id="hits"), params, 5)
    assert {i.id for i in picked} <= {i.id for i in items[40:]}
    query = RandomSource().pool_query(params, domain="music", playable_for=None, preset_id="hits")
    assert query.popularity_top_pct == 20
    assert (
        RandomSource().pool_query(params, domain="music", playable_for=None).popularity_top_pct
        is None
    )


async def test_top_percent_is_scale_independent_between_domains() -> None:
    films = [media_item(f"F{i}", domain="film", kind="film", popularity=10 + i) for i in range(20)]
    tracks = _tracks(20, scale=50_000.0)
    port = FakeCandidatePort([*films, *tracks])
    top_films = await port.candidates(PoolQuery(domain="film", popularity_top_pct=25), 100)
    top_tracks = await port.candidates(PoolQuery(domain="music", popularity_top_pct=25), 100)
    assert len(top_films) == len(top_tracks) == 5
