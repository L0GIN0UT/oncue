"""Мягкий фильтр популярности в рейтинговых музыкальных режимах (ADR 0075).

Тесты читают настоящие сиды ``music.ranked.*`` и проверяют на выборке с узкой вилкой
рангов (как у Deezer: 615 тыс.-1 млн), что отсечение нижней половины вместе с весами
``sqrt`` заметно сдвигает розыгрыш к известным трекам, а пул остаётся играбельным.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

import pytest
import yaml

from gv_core.random import SeededRandom
from gv_game_kernel import MediaItemRef, PoolQuery
from gv_game_kernel.builtin import RandomParams, RandomSource
from gv_game_kernel.builtin.sources import _sample
from gv_testkit.game import FakeCandidatePort, media_item
from gv_testkit.match import build_online_engine

pytestmark = pytest.mark.unit

MODES = Path(__file__).resolve().parents[3] / "seeds/game/modes"
RANKED = ["music.ranked.classic", "music.ranked.lite"]
LOW, HIGH = 615_000.0, 1_000_000.0
TRACKS = 400
DRAWS = 200


def _seed(key: str) -> dict[str, Any]:
    body: dict[str, Any] = yaml.safe_load((MODES / f"{key}.yaml").read_text(encoding="utf-8"))
    return body


def _tracks() -> list[MediaItemRef]:
    """Треки с рангом в узкой вилке; год в окне дистракторов, названия различаются."""
    step = (HIGH - LOW) / (TRACKS - 1)
    return [
        media_item(f"Track {i}", year=2005 + i % 10, popularity=LOW + i * step)
        for i in range(TRACKS)
    ]


def _pool(key: str) -> dict[str, Any]:
    pool: dict[str, Any] = _seed(key)["source"]["params"]["pool"]
    return pool


@pytest.mark.parametrize("key", RANKED)
def test_ranked_seeds_take_top_half_with_sqrt_weights(key: str) -> None:
    pool = _pool(key)
    assert pool["popularity_top_pct"] == 50
    assert pool["popularity_weight"] == "sqrt"
    assert pool["min_playable"] == 20


def test_duel_seeds_are_not_touched() -> None:
    classic, lite = _pool("music.duel.classic"), _pool("music.duel.lite")
    assert (classic["popularity_top_pct"], classic["popularity_weight"]) == (30, "sqrt")
    assert "popularity_top_pct" not in lite and "popularity_weight" not in lite


def test_weights_alone_barely_move_a_narrow_rank_band() -> None:
    """Объясняет, зачем нужно отсечение: корень на узкой вилке почти не различает треки."""
    items = _tracks()
    weighted = [
        mean(i.popularity or 0 for i in _sample(items, 7, SeededRandom(f"w{n}".encode()), "sqrt"))
        for n in range(DRAWS)
    ]
    plain = [
        mean(i.popularity or 0 for i in _sample(items, 7, SeededRandom(f"w{n}".encode())))
        for n in range(DRAWS)
    ]
    # Ранг, а не сама метрика, но без порога весов хватает лишь на умеренный сдвиг.
    assert mean(weighted) - mean(plain) < 0.25 * (HIGH - LOW) / 2


@pytest.mark.parametrize("key", RANKED)
async def test_ranked_draw_skews_to_popular_and_keeps_pool_playable(key: str) -> None:
    items = _tracks()
    pool = _pool(key)
    with_filter = RandomParams.model_validate({"pool": pool})
    without = RandomParams.model_validate(
        {
            "pool": {
                k: v
                for k, v in pool.items()
                if k not in {"popularity_top_pct", "popularity_weight"}
            }
        }
    )
    engine = build_online_engine(_seed(key))
    query = engine.pool_query()
    assert query is not None and query.popularity_top_pct == 50

    port = FakeCandidatePort(items)
    top_pool = await port.candidates(PoolQuery(domain="music", popularity_top_pct=50), 10_000)
    assert len(top_pool) >= pool["min_playable"]

    async def mean_popularity(params: RandomParams) -> float:
        picked: list[float] = []
        for n in range(DRAWS):
            ctx = engine.draw_context(seed=f"s{n}".encode(), candidates=port)
            drawn = await RandomSource().draw(ctx, params, 7)
            picked.extend(i.popularity or 0 for i in drawn)
        return mean(picked)

    boosted = await mean_popularity(with_filter)
    plain = await mean_popularity(without)
    assert boosted > plain + 0.15 * (HIGH - LOW)


async def test_ranked_lite_still_finds_distractors_in_the_trimmed_pool() -> None:
    engine = build_online_engine(_seed("music.ranked.lite"))
    port = FakeCandidatePort(_tracks())
    ctx = engine.draw_context(seed=b"lite", candidates=port)
    draws = await engine.draw_rounds(ctx)
    assert len(draws) == _seed("music.ranked.lite")["session"]["rounds"]
    top = {
        i.id
        for i in await port.candidates(PoolQuery(domain="music", popularity_top_pct=50), 10_000)
    }
    for draw in draws:
        assert len(draw.options) == 4
        assert {o.item.id for o in draw.options} <= top
