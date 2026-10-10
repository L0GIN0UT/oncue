"""``ModeEngine`` — фасад для game — и DSL сценариев ``gv_testkit.game`` на сидах."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest

from gv_core.errors import GoneError
from gv_game_kernel import (
    AnswerInput,
    LevelLockedError,
    MatchVerdict,
    PoolQuery,
    PresetNotFoundError,
    RoundFinishedError,
    RoundNotFinishedError,
    RoundResult,
    daily_seed,
)
from gv_testkit.game import (
    FakeAssetPort,
    FakeCandidatePort,
    asset_ref,
    build_engine,
    given_mode,
    media_item,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]


@pytest.fixture
def track() -> Any:
    return media_item("Yesterday", aliases=["yesterday"], artists=["The Beatles"])


# ----------------------------------------------------------------------------- DSL на сидах
def test_daily_music_round_flow(seed_body: SeedBody, track: Any) -> None:
    """Демонстрация §0 п. 2: неверно → уровень 2, пропуск → 3, верно → 4 очка."""
    (
        given_mode(seed_body("music.daily.classic"))
        .with_items(track)
        .when_guess(text="Michelle")
        .then_round(outcome=None, level=2, last_match="miss")
        .when_skip()
        .then_round(outcome=None, level=3)
        .when_guess(item=track)
        .then_round(outcome="exact", level=3, points=4)
        .then_score(4, max_score=6)
    )


def test_partial_by_artist_counts_once(seed_body: SeedBody, track: Any) -> None:
    (
        given_mode(seed_body("music.daily.classic"))
        .with_items(track)
        .when_guess(text="The Beatles")
        .then_round(outcome=None, level=2, partial_level=1, last_match="partial")
        .when_guess(text="beatles")
        .then_round(partial_level=1)
        .when_give_up()
        .then_round(outcome="gave_up", points=3)  # floor(6 × 0.5)
    )


def test_full_session_with_void_round(seed_body: SeedBody) -> None:
    items = [media_item(f"Film {i}", domain="film", aliases=[f"film {i}"]) for i in range(5)]
    scenario = given_mode(seed_body("film.daily.frame")).with_items(*items)
    scenario.when_guess(text="film 0")  # 6
    scenario.when_skip().when_guess(text="film 1")  # 5
    scenario.when_void()
    for _ in range(6):
        scenario.when_skip()  # miss
    scenario.when_give_up()
    scenario.then_round(index=2, outcome="void").then_round(index=3, outcome="miss")
    scenario.then_score(11, max_score=24)


def test_scenario_guards(seed_body: SeedBody, track: Any) -> None:
    scenario = given_mode(seed_body("music.daily.classic"))
    with pytest.raises(AssertionError, match="ходов"):
        scenario.then_round(outcome=None)
    with pytest.raises(AssertionError, match="раундов"):
        scenario.when_skip()
    scenario.with_items(track).when_guess(item_id=track.id).then_round(outcome="exact", points=6)
    with pytest.raises(AssertionError, match="очки"):
        scenario.then_score(5)


# ----------------------------------------------------------------------------- движок
def test_engine_static_view(seed_body: SeedBody) -> None:
    # Пул и пресеты сида подстраиваются под seeds/catalog, поэтому сверяемся с самим сидом,
    # а неизменные свойства загадки (уровни, очки за раунд) проверяем явно.
    body = seed_body("music.random.classic")
    engine = build_engine(body)
    assert [lvl.hint["seconds"] for lvl in engine.levels()] == [0.5, 1, 2, 4, 8, 15]
    assert engine.levels_count == 6
    assert engine.playable_for.clue_type == "audio_intro"
    assert len(engine.clue_params_hash) == 64
    assert [p.id for p in engine.presets()] == [
        p["id"] for p in body["source"]["params"]["presets"]
    ]
    rounds = body["session"]["rounds"]
    assert engine.min_playable() == body["source"]["params"]["pool"]["min_playable"]
    assert engine.round_max_score() == 6
    assert engine.max_score() == 6 * rounds and engine.max_score(3) == 18
    with pytest.raises(ValueError, match="отрицательным"):
        engine.max_score(-1)
    rock = next(p for p in body["source"]["params"]["presets"] if p["id"] == "rock")
    query = engine.pool_query("rock")
    assert isinstance(query, PoolQuery)
    assert query.genres_any == frozenset(rock["filters"]["genres_any"])
    assert query.playable_for == engine.playable_for
    with pytest.raises(PresetNotFoundError):
        engine.pool_query("jazz")


async def test_engine_draw_uses_mode_context(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("music.daily.classic"))
    items = [media_item(f"T{i}", aliases=[f"t{i}"]) for i in range(12)]
    port = FakeCandidatePort(items)
    seed = daily_seed(b"secret", engine.config.key, date(2026, 10, 10))
    ctx = engine.draw_context(seed=seed, candidates=port)
    first = await engine.draw(ctx)
    assert len(first) == 5 and first == await engine.draw(ctx)
    assert port.queries[0][0].playable_for == engine.playable_for
    other = build_engine(seed_body("music.random.classic"))
    with pytest.raises(ValueError, match="другого режима"):
        await other.draw(ctx)


async def test_clue_access_follows_round_state(seed_body: SeedBody, track: Any) -> None:
    engine = build_engine(seed_body("music.daily.classic"))
    assets, ref = FakeAssetPort(), asset_ref(6)
    state = engine.new_round()
    view = await engine.clue_view(state, ref, 1, assets=assets, subject="sess:1")
    assert view.kind == "audio" and view.meta == {"seconds": 0.5}
    with pytest.raises(LevelLockedError):
        await engine.clue_view(state, ref, 2, assets=assets, subject="sess:1")
    with pytest.raises(RoundNotFinishedError):
        await engine.final_clue(state, ref, assets=assets, subject="sess:1")
    state = engine.skip(state)
    await engine.clue_view(state, ref, 2, assets=assets, subject="sess:1")
    state = engine.answer(state, AnswerInput(text="yesterday"), track, display="yesterday").state
    final = await engine.final_clue(state, ref, assets=assets, subject="sess:1")
    assert final.level == 6
    assert assets.levels_for("sess:1") == [1, 2, 6]


async def test_gone_asset_makes_round_void(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.daily.frame"))
    assets, ref = FakeAssetPort(), asset_ref(6)
    assets.gone(ref.asset_set_id)
    state = engine.new_round()
    with pytest.raises(GoneError):
        await engine.clue_view(state, ref, 1, assets=assets, subject="sess:1")
    state = engine.void(state)
    assert engine.score(engine.outcome(state)) == 0
    assert engine.outcome(state).result is RoundResult.VOID


def test_answer_in_finished_round_is_rejected_before_matching(
    seed_body: SeedBody, track: Any
) -> None:
    engine = build_engine(seed_body("music.daily.classic"))
    step = engine.answer(engine.new_round(), AnswerInput(text="Yesterday"), track, display="y")
    assert step.match.verdict is MatchVerdict.EXACT
    with pytest.raises(RoundFinishedError):
        engine.answer(step.state, AnswerInput(text="Yesterday"), track, display="y")
    engine.require_level_open(step.state, 6)


async def test_fake_asset_port_failures_and_heal() -> None:
    from gv_core.errors import DependencyUnavailableError, RateLimitedError

    assets, ref = FakeAssetPort(), asset_ref(6)
    assets.unavailable()
    with pytest.raises(DependencyUnavailableError):
        await assets.link(ref, 1, "s")
    assets.rate_limited()
    with pytest.raises(RateLimitedError):
        await assets.link(ref, 1, "s")
    assets.heal()
    link = await assets.link(ref, 1, "s")
    assert link.url.startswith("/media/a/") and assets.levels_for("s") == [1]


def test_answer_selected_must_match_id(track: Any) -> None:
    with pytest.raises(ValueError, match="selected"):
        AnswerInput(media_item_id=media_item("Other").id, selected=track)


def test_pool_query_rejects_disjoint_preset() -> None:
    from gv_game_kernel.builtin.pool import PoolFilters, PoolParams, build_query

    with pytest.raises(ValueError, match="не пересекается"):
        build_query(
            PoolParams(kinds=("track",)),
            domain="music",
            playable_for=None,
            extra=PoolFilters(kinds=("film",)),
        )
