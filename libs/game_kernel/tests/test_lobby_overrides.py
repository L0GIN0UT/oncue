"""Переопределения хозяина лобби ``clue`` и ``answer.format`` (ADR 0070)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gv_game_kernel import (
    ONLINE_SESSIONS,
    EngineFactory,
    OverrideNotAllowedError,
    PluginRegistry,
    apply_lobby_overrides,
    parse_mode_config,
    register_builtin,
    register_collection,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
FACTORY = EngineFactory(
    register_collection(register_builtin(PluginRegistry())), supported_sessions=ONLINE_SESSIONS
)
POSTER_LEVELS = [{"pixel_block": p} for p in (64, 32, 16, 8, 4, 1)]


@pytest.fixture
def lobby(seed_body: SeedBody) -> dict[str, Any]:
    body = seed_body("film.lobby.collection")
    over = body["session"]["lobby"]["overridable"]
    over["answer_format"] = ["classic"]
    over["clue"] = [
        body["clue"],
        {"type": "poster_masked", "params": {"levels": POSTER_LEVELS}},
    ]
    return body


def test_no_overrides_returns_equal_copy(lobby: dict[str, Any]) -> None:
    result = apply_lobby_overrides(lobby)
    assert result == lobby and result is not lobby


def test_clue_taken_from_allowed_variants_without_mutating_input(lobby: dict[str, Any]) -> None:
    variant = lobby["session"]["lobby"]["overridable"]["clue"][1]
    before = repr(lobby)
    result = apply_lobby_overrides(lobby, clue_type="poster_masked")
    assert result["clue"] == variant
    assert repr(lobby) == before
    assert FACTORY.validate(parse_mode_config(result)) == []


def test_template_clue_type_is_noop(lobby: dict[str, Any]) -> None:
    assert apply_lobby_overrides(lobby, clue_type=lobby["clue"]["type"]) == lobby


def test_unknown_clue_is_not_allowed(lobby: dict[str, Any]) -> None:
    with pytest.raises(OverrideNotAllowedError) as info:
        apply_lobby_overrides(lobby, clue_type="audio_intro")
    assert info.value.code == "game.override_not_allowed"
    assert info.value.details == {"field": "clue"}


def test_format_must_be_listed(lobby: dict[str, Any]) -> None:
    with pytest.raises(OverrideNotAllowedError) as info:
        apply_lobby_overrides(lobby, answer_format="pick")
    assert info.value.details == {"field": "answer_format"}


def test_format_from_list_applies(lobby: dict[str, Any]) -> None:
    lobby["session"]["lobby"]["overridable"]["answer_format"] = ["classic", "pick"]
    result = apply_lobby_overrides(lobby, answer_format="pick")
    assert result["answer"]["format"] == "pick"


def test_mode_without_lobby_allows_nothing(seed_body: SeedBody) -> None:
    body = seed_body("film.duel.frame")
    with pytest.raises(OverrideNotAllowedError):
        apply_lobby_overrides(body, answer_format="pick")
    with pytest.raises(OverrideNotAllowedError):
        apply_lobby_overrides(body, clue_type="poster_masked")


def test_incompatible_combination_is_left_to_validator(lobby: dict[str, Any]) -> None:
    """Ядро не дублирует валидатор: ``pick`` + ``race`` отвергает ``EngineFactory.validate``."""
    lobby["session"]["lobby"]["overridable"]["answer_format"] = ["classic", "pick"]
    result = apply_lobby_overrides(lobby, answer_format="pick")
    codes = {i.code for i in FACTORY.validate(parse_mode_config(result))}
    assert "config.answer_format_unsupported" in codes
