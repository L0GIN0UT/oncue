"""Дистракторы формата ``pick`` в движке и валидаторе (ADR 0040): варианты раунда, античит,
перекрёстные правила ``answer.params.distractors``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gv_game_kernel import (
    ALL_SESSIONS,
    ONLINE_SESSIONS,
    AnswerInput,
    ConfigIssue,
    EngineFactory,
    PluginRegistry,
    PoolTooSmallError,
    parse_mode_config,
    register_builtin,
)
from gv_testkit.game import FakeCandidatePort, media_item

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
FACTORY = EngineFactory(register_builtin(PluginRegistry()), supported_sessions=ONLINE_SESSIONS)
FACTORY_ALL = EngineFactory(register_builtin(PluginRegistry()), supported_sessions=ALL_SESSIONS)
SIMILAR = {
    "same_genre": False,
    "year_window": None,
    "popularity_ratio": None,
    "min_pool": 8,
}


def lite_body(seed_body: SeedBody, **over: Any) -> dict[str, Any]:
    """Дуэль «лайт»: один уровень, выбор из 4 вариантов, одна попытка (ADR 0040)."""
    body = seed_body("music.duel.classic")
    body["key"] = "music.duel.litetest"
    body["clue"]["params"]["levels"] = [{"seconds": 4}]
    body["scoring"]["params"]["points"] = [12]
    body["answer"]["format"] = "pick"
    body["answer"]["partial"] = []
    body["answer"]["params"] = {
        "distractors": {"type": "similar", "count": over.get("count", 3), "params": dict(SIMILAR)}
    }
    body["session"]["level_windows_s"] = [20]
    body["session"]["contest"] = {
        "type": "parallel",
        "params": {"attempts_per_level": 1, "end_when_all_answered": True},
    }
    return body


def issues(body: dict[str, Any]) -> list[ConfigIssue]:
    return FACTORY.validate(parse_mode_config(body))


def test_lite_config_valid(seed_body: SeedBody) -> None:
    assert issues(lite_body(seed_body)) == []


def test_distractors_only_with_pick_format(seed_body: SeedBody) -> None:
    body = lite_body(seed_body)
    body["answer"]["format"] = "classic"
    assert ConfigIssue("answer.params.distractors", "config.distractors_format") in issues(body)


def test_unknown_distractor_type(seed_body: SeedBody) -> None:
    body = lite_body(seed_body)
    body["answer"]["params"]["distractors"]["type"] = "nope"
    assert ConfigIssue("answer.params.distractors.type", "config.unknown_plugin") in issues(body)


def test_distractor_params_schema(seed_body: SeedBody) -> None:
    body = lite_body(seed_body)
    body["answer"]["params"]["distractors"]["params"] = {"min_pool": 0}
    paths = {i.path for i in issues(body)}
    assert "answer.params.distractors.params.min_pool" in paths


@pytest.mark.parametrize(("min_pool", "ok"), [(4, True), (3, False)])
def test_count_plus_one_within_min_pool(seed_body: SeedBody, min_pool: int, ok: bool) -> None:
    body = lite_body(seed_body, count=3)
    body["answer"]["params"]["distractors"]["params"]["min_pool"] = min_pool
    expected = ConfigIssue(
        "answer.params.distractors.params.min_pool", "config.distractor_pool_too_small"
    )
    assert (expected not in issues(body)) is ok


# ------------------------------------------------------------------ розыгрыш
def _engine(seed_body: SeedBody) -> Any:
    return FACTORY.build(parse_mode_config(lite_body(seed_body)))


def _ctx(engine: Any, items: list[Any], seed: bytes = b"seed") -> Any:
    return engine.draw_context(seed=seed, candidates=FakeCandidatePort(items))


async def test_draw_rounds_builds_options(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    items = [media_item(f"T{i}") for i in range(30)]
    draws = await engine.draw_rounds(_ctx(engine, items), 5)
    assert len(draws) == 5
    for draw in draws:
        assert len(draw.options) == 4
        by_item = [o.item.id for o in draw.options if o.item is not None]
        assert len(set(by_item)) == 4
        assert draw.target.id in by_item
        assert len({o.id for o in draw.options}) == 4


async def test_options_deterministic_by_seed(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    items = [media_item(f"T{i}") for i in range(30)]

    def shape(draws: list[Any]) -> list[Any]:
        return [[(o.id, o.item.id) for o in d.options] for d in draws]

    a = shape(await engine.draw_rounds(_ctx(engine, items, b"a"), 3))
    b = shape(await engine.draw_rounds(_ctx(engine, list(reversed(items)), b"a"), 3))
    c = shape(await engine.draw_rounds(_ctx(engine, items, b"c"), 3))
    assert a == b
    assert a != c


async def test_correct_option_not_derivable(seed_body: SeedBody) -> None:
    """Верный вариант не угадывается ни по id, ни по позиции (статистически по seed-ам)."""
    engine = _engine(seed_body)
    items = [media_item(f"T{i}") for i in range(40)]
    positions: set[int] = set()
    for n in range(40):
        (draw,) = await engine.draw_rounds(_ctx(engine, items, f"s{n}".encode()), 1)
        positions.add(next(i for i, o in enumerate(draw.options) if o.item == draw.target))
        assert all(o.id not in (draw.target.title, str(draw.target.id)) for o in draw.options)
    assert positions == {0, 1, 2, 3}


async def test_pool_too_small_for_options(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    items = [media_item(f"T{i}") for i in range(5)]
    with pytest.raises(PoolTooSmallError):
        await engine.draw_rounds(_ctx(engine, items), 1)


async def test_pick_answer_matches_option_item(seed_body: SeedBody) -> None:
    engine = _engine(seed_body)
    items = [media_item(f"T{i}") for i in range(30)]
    (draw,) = await engine.draw_rounds(_ctx(engine, items), 1)
    right = next(o for o in draw.options if o.item == draw.target)
    wrong = next(o for o in draw.options if o.item != draw.target)
    assert engine.check(AnswerInput(option_id=right.id), draw.target, draw).verdict == "exact"
    assert engine.check(AnswerInput(option_id=wrong.id), draw.target, draw).verdict == "miss"


def test_min_playable_covers_options(seed_body: SeedBody) -> None:
    body = lite_body(seed_body, count=7)
    body["session"]["rounds"] = 1
    body["source"]["params"]["pool"]["min_playable"] = 2
    body["answer"]["params"]["distractors"]["params"]["min_pool"] = 8
    engine = FACTORY.build(parse_mode_config(body))
    assert engine.min_playable() == 8


def test_distractors_with_round_source_rejected(seed_body: SeedBody) -> None:
    body = seed_body("music.pair.popularity")
    body["answer"]["params"] = {
        "distractors": {"type": "similar", "count": 3, "params": dict(SIMILAR)}
    }
    assert ConfigIssue(
        "answer.params.distractors", "config.distractors_unsupported_source"
    ) in FACTORY_ALL.validate(parse_mode_config(body))


def test_pick_without_options_source_needs_distractors(seed_body: SeedBody) -> None:
    body = lite_body(seed_body)
    del body["answer"]["params"]
    assert ConfigIssue("answer.params.distractors", "config.options_source_missing") in issues(body)


def test_pick_with_round_source_needs_no_distractors(seed_body: SeedBody) -> None:
    body = seed_body("music.pair.popularity")
    codes = {i.code for i in FACTORY_ALL.validate(parse_mode_config(body))}
    assert "config.options_source_missing" not in codes


def test_classic_format_without_distractors_unaffected(seed_body: SeedBody) -> None:
    assert FACTORY.validate(parse_mode_config(seed_body("music.duel.classic"))) == []


async def test_distractor_pool_follows_preset_filters(seed_body: SeedBody) -> None:
    """Пул дистракторов сужается фильтрами пресета, как и розыгрыш загаданных."""
    body = lite_body(seed_body)
    body["source"]["params"]["presets"] = [
        {"id": "rock", "title_i18n": "p.rock", "filters": {"genres_any": ["rock"]}}
    ]
    engine = FACTORY.build(parse_mode_config(body))
    rock = [media_item(f"R{i}", genres=("rock",)) for i in range(12)]
    jazz = [media_item(f"J{i}", genres=("jazz",)) for i in range(12)]
    ctx = engine.draw_context(
        seed=b"p", candidates=FakeCandidatePort([*rock, *jazz]), preset_id="rock"
    )
    for draw in await engine.draw_rounds(ctx, 3):
        assert {o.item.id for o in draw.options if o.item} <= {i.id for i in rock}
