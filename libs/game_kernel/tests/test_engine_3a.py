"""``ModeEngine`` этапа 3a: состав раунда, ответы ``pick``, ревил другим типом, сессия
с «жизнями» — и регресс розыгрыша ``daily``/``random`` этапа 2."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gv_core.errors import GoneError
from gv_game_kernel import (
    AnswerInput,
    AnswerKindError,
    GuessKind,
    MatchVerdict,
    OptionNotFoundError,
    RoundDraw,
    RoundNotFinishedError,
    daily_seed,
    params_hash,
)
from gv_testkit.game import (
    FakeAssetPort,
    FakeCandidatePort,
    asset_ref,
    build_engine,
    given_mode,
    media_item,
    person,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]


def _rated(*values: float) -> list[Any]:
    return [
        media_item(f"Film {i}", domain="film", year=1990 + i, metrics={"vote_average": v})
        for i, v in enumerate(values)
    ]


# ----------------------------------------------------------------------------- регресс
@pytest.mark.parametrize("key", ["music.daily.classic", "music.random.classic"])
async def test_classic_draw_rounds_wraps_draw(key: str, seed_body: SeedBody) -> None:
    """Адаптер: ``draw_rounds`` — те же единицы, что ``draw`` этапа 2, по одной на раунд."""
    engine = build_engine(seed_body(key))
    items = [media_item(f"T{i}", aliases=[f"t{i}"], genres=["rock"]) for i in range(40)]
    seed = daily_seed(b"secret", engine.config.key, date(2026, 10, 10))
    ctx = engine.draw_context(seed=seed, candidates=FakeCandidatePort(items))
    drawn = await engine.draw(ctx)
    rounds = await engine.draw_rounds(ctx)
    assert rounds == [RoundDraw(target=item) for item in drawn]
    assert rounds == await engine.draw_rounds(ctx)
    assert engine.compose_rounds(drawn) == rounds
    assert not engine.is_progressive and engine.draw_size == engine.config.session.rounds
    assert engine.options_view(rounds[0]) == [] and engine.shown_view(rounds[0]) == []


async def test_daily_draw_is_unchanged_by_stage3a(seed_body: SeedBody) -> None:
    """Сиды этапа 2: набор дня по фиксированному seed не меняется (регресс розыгрыша)."""
    engine = build_engine(seed_body("music.daily.classic"))
    items = [media_item(f"T{i}", aliases=[f"t{i}"]) for i in range(12)]
    ctx = engine.draw_context(seed=b"fixed-seed", candidates=FakeCandidatePort(items))
    picked = [int(i.id) for i in await engine.draw(ctx)]
    from gv_core.random import SeededRandom

    expected = SeededRandom(b"fixed-seed").sample(sorted(items, key=lambda i: i.id), 5)
    assert picked == [int(i.id) for i in expected]
    recent = frozenset(i.id for i in items[:9])
    ctx = engine.draw_context(
        seed=b"fixed-seed", candidates=FakeCandidatePort(items), exclude_ids=recent
    )
    again = await engine.draw(ctx)
    assert {i.id for i in again} >= {i.id for i in items[9:]}


# ----------------------------------------------------------------------------- ответы
def test_answer_kind_must_match_format(seed_body: SeedBody) -> None:
    pair = build_engine(seed_body("film.pair.rating"))
    a, b = _rated(5.0, 7.0)
    draw = pair.compose_rounds([a, b])[0]
    with pytest.raises(AnswerKindError) as info:
        pair.answer(pair.new_round(), AnswerInput(text="Film 1"), b, display="x", draw=draw)
    assert info.value.code == "game.answer_kind_unsupported"
    assert info.value.details == {"kind": "text", "allowed": ["option"]}
    with pytest.raises(OptionNotFoundError) as unknown:
        pair.answer(pair.new_round(), AnswerInput(option_id="up"), b, display="x", draw=draw)
    assert unknown.value.code == "game.option_unknown"
    with pytest.raises(ValueError, match="draw"):
        pair.check(AnswerInput(option_id="higher"), b)
    classic = build_engine(seed_body("music.daily.classic"))
    track = media_item("Song")
    with pytest.raises(AnswerKindError) as option_in_classic:
        classic.check(AnswerInput(option_id="higher"), track)
    assert option_in_classic.value.details["allowed"] == ["item", "text"]


def test_pick_answer_step(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.pair.rating"))
    a, b = _rated(5.0, 7.0)
    draw = engine.compose_rounds([a, b])[0]
    step = engine.answer(
        engine.new_round(), AnswerInput(option_id="higher"), b, display="выше", draw=draw
    )
    assert step.match.verdict is MatchVerdict.EXACT
    assert step.state.finished and step.state.guesses[0].kind is GuessKind.OPTION
    assert step.option is not None and step.option.id == "higher"
    assert engine.option(draw, "lower").label_key == "game.options.lower"


@settings(max_examples=50, deadline=None)
@given(
    anchor=st.integers(min_value=0, max_value=100),
    target=st.integers(min_value=0, max_value=100),
    choice=st.sampled_from(["higher", "lower"]),
)
def test_pick_round_always_finishes_with_one_answer(anchor: int, target: int, choice: str) -> None:
    """Формат ``pick`` (один уровень, одна попытка): раунд кончается первым же ответом."""
    from pathlib import Path

    import yaml

    body = yaml.safe_load(
        (Path(__file__).parent / "modes" / "film.pair.rating.yaml").read_text(encoding="utf-8")
    )
    engine = build_engine(body)
    a, b = _rated(anchor / 10, target / 10)
    draw = engine.compose_rounds([a, b])[0]
    answer = AnswerInput(option_id=choice)
    step = engine.answer(engine.new_round(), answer, b, display=choice, draw=draw)
    assert step.state.finished
    right = anchor == target or (choice == "higher") == (target > anchor)
    assert step.match.verdict is (MatchVerdict.EXACT if right else MatchVerdict.MISS)


# ----------------------------------------------------------------------------- античит
def test_pair_views_hide_target(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.pair.rating"))
    a, b = _rated(6.4, 8.2)
    draw = engine.compose_rounds([a, b])[0]
    options = engine.options_view(draw)
    assert [(o.id, o.label_key, o.title) for o in options] == [
        ("higher", "game.options.higher", None),
        ("lower", "game.options.lower", None),
    ]
    shown = engine.shown_view(draw)
    assert len(shown) == 1 and shown[0].item == a
    assert shown[0].metric == 6.4 and shown[0].option_id is None
    assert engine.metric_of(b) == 8.2  # для ревила, после ответа


def test_bracket_views_link_shown_to_options(seed_body: SeedBody) -> None:
    body = seed_body("film.bracket.weekly")
    body["source"]["params"].update(size=8)
    body["source"]["params"]["subject"]["min_items"] = 8
    body["session"]["rounds"] = 7
    engine = build_engine(body)
    items = [media_item(f"F{i}", domain="film") for i in range(8)]
    draw = engine.compose_rounds(items)[1]
    assert [(o.id, o.title) for o in engine.options_view(draw)] == [("a", "F2"), ("b", "F3")]
    assert [(s.option_id, s.metric) for s in engine.shown_view(draw)] == [("a", None), ("b", None)]
    assert engine.metric_name is None and engine.metric_of(items[0]) is None
    assert engine.presentation == "bracket" and engine.round_stage(0) == 8


# ----------------------------------------------------------------------------- ревил
async def test_final_clue_uses_reveal_asset(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.random.blackout"))
    reveal = engine.config.clue.reveal
    assert reveal is not None
    assert engine.reveal_playable_for is not None
    assert engine.reveal_playable_for.clue_type == "frame_progressive"
    assert engine.reveal_playable_for.params_hash == params_hash(reveal.params)
    assets, sound, frame = FakeAssetPort(), asset_ref(6), asset_ref(1)
    state = engine.new_round()
    level1 = await engine.clue_view(state, sound, 1, assets=assets, subject="sess:1")
    assert level1.kind == "audio"
    with pytest.raises(RoundNotFinishedError):
        await engine.final_clue(state, sound, assets=assets, subject="sess:1", reveal_asset=frame)
    state = engine.give_up(state)
    view = await engine.final_clue(
        state, sound, assets=assets, subject="sess:1", reveal_asset=frame
    )
    assert (view.kind, view.level, dict(view.meta)) == ("image", 1, {"blur_px": 0.0, "crop": 1.0})
    assert assets.issued[-1].asset_set_id == frame.asset_set_id
    with pytest.raises(GoneError):
        await engine.final_view(sound, assets=assets, subject="sess:1")


async def test_final_view_without_reveal_is_last_level(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.random.frame"))
    assert engine.reveal_playable_for is None
    assets, ref = FakeAssetPort(), asset_ref(6)
    view = await engine.final_view(ref, assets=assets, subject="s", reveal_asset=asset_ref(1))
    assert view.level == 6 and assets.issued[-1].asset_set_id == ref.asset_set_id


# ----------------------------------------------------------------------------- сессия, DSL
async def test_pair_session_ends_on_first_miss(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("film.pair.rating"))
    films = _rated(5.0, 7.0, 6.0, 9.0)
    draws = engine.compose_rounds(films)
    scenario = (
        given_mode(engine)
        .with_draws(*draws)
        .then_lives(1)
        .when_pick("higher")
        .then_round(outcome="exact", points=1)
        .then_streak(1)
        .then_session_ended(ended=False)
        .when_pick("higher")  # 7.0 → 6.0: неверно
        .then_round(outcome="miss")
        .then_lives(0)
        .then_streak(0)
        .then_session_ended()
        .then_score(1)
    )
    assert engine.presentation == "streak"
    with pytest.raises(AssertionError, match="завершена"):
        scenario.when_pick("lower")


async def test_marathon_session_ends_after_three_losses(seed_body: SeedBody) -> None:
    engine = build_engine(seed_body("music.marathon.artist"))
    artist = person("Artist")
    tracks = [media_item(f"Song {i}", aliases=[f"song {i}"], persons=[artist]) for i in range(10)]
    port = FakeCandidatePort(tracks)
    ctx = engine.draw_context(seed=b"m", candidates=port, subject_ids=frozenset({artist.id}))
    scenario = given_mode(engine).with_draws(*await engine.draw_rounds(ctx))
    assert len(scenario.rounds) == 10
    scenario.when_guess(text=scenario.current().item.title).then_lives(3)
    scenario.when_give_up().when_give_up().then_lives(1).then_session_ended(ended=False)
    scenario.when_void().then_lives(1)
    scenario.when_give_up().then_lives(0).then_session_ended()
    with pytest.raises(AssertionError, match="завершена"):
        scenario.when_skip()
