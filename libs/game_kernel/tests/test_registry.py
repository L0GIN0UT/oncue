"""Реестр плагинов, проверка ключей конфига и сборка движка на фейковых плагинах."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from gv_game_kernel import (
    UNKNOWN_PLUGIN,
    ConfigError,
    ConfigIssue,
    DuplicatePluginError,
    EngineFactory,
    ModeConfig,
    ModeEngine,
    PluginRegistry,
)

pytestmark = pytest.mark.unit


class _Params(BaseModel):
    pass


class FakeClue:
    key: ClassVar[str] = "audio_intro"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})
    params_schema: ClassVar[type[BaseModel]] = _Params
    supported_answer_formats: ClassVar[frozenset[str]] = frozenset({"classic"})

    def levels(self, params: BaseModel) -> list[Any]:
        return []

    async def reveal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def final_view(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class FakeSource:
    key: ClassVar[str] = "daily"
    params_schema: ClassVar[type[BaseModel]] = _Params

    async def draw(self, ctx: Any, params: BaseModel, n: int) -> list[Any]:
        return []

    def presets(self, params: BaseModel) -> list[Any]:
        return []

    def cross_check(self, params: BaseModel, **kwargs: Any) -> list[Any]:
        return []

    def pool_query(self, params: BaseModel, **kwargs: Any) -> None:
        return None

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        return rounds


class FakeMatcher:
    key: ClassVar[str] = "title_alias"
    params_schema: ClassVar[type[BaseModel]] = _Params

    def match(self, answer: Any, item: Any, params: BaseModel, ctx: Any) -> Any:
        raise NotImplementedError


class FakePartial:
    kind: ClassVar[str] = "artist"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})

    def check(self, answer: Any, item: Any, ctx: Any) -> bool:
        return False


class FakeScorer:
    key: ClassVar[str] = "by_level"
    params_schema: ClassVar[type[BaseModel]] = _Params

    def score(self, outcome: Any, params: BaseModel) -> int:
        return 0

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        return 0

    def cross_check(self, params: BaseModel, **kwargs: Any) -> list[Any]:
        return []


def _registry() -> PluginRegistry:
    r = PluginRegistry()
    r.register_clue(FakeClue())
    r.register_source(FakeSource())
    r.register_matcher(FakeMatcher())
    r.register_partial(FakePartial())
    r.register_scorer(FakeScorer())
    return r


def _config(**overrides: Any) -> ModeConfig:
    body: dict[str, Any] = {
        "key": "music.daily.classic",
        "title_i18n": "modes.music.daily.classic.title",
        "domain": "music",
        "source": {"type": "daily", "params": {}},
        "clue": {"type": "audio_intro", "params": {}},
        "answer": {
            "format": "classic",
            "matcher": {"type": "title_alias", "params": {}},
            "partial": ["artist"],
        },
        "scoring": {"type": "by_level", "params": {}},
        "session": {
            "kind": "solo",
            "rounds": 5,
            "attempts_per_level": 1,
            "skip_allowed": True,
            "give_up_allowed": True,
            "allow_guest": True,
            "abandon_after_min": 60,
        },
    }
    body.update(overrides)
    return ModeConfig.model_validate(body)


@pytest.mark.parametrize(
    ("register", "plugin"),
    [
        ("register_clue", FakeClue()),
        ("register_source", FakeSource()),
        ("register_matcher", FakeMatcher()),
        ("register_partial", FakePartial()),
        ("register_scorer", FakeScorer()),
    ],
)
def test_duplicate_key_is_rejected(register: str, plugin: object) -> None:
    registry = _registry()
    with pytest.raises(DuplicatePluginError, match="уже зарегистрирован"):
        getattr(registry, register)(plugin)


def test_lookup_by_key() -> None:
    r = _registry()
    assert isinstance(r.clue("audio_intro"), FakeClue)
    assert isinstance(r.partial("artist"), FakePartial)
    assert r.scorer("by_speed") is None


def test_valid_config_has_no_issues_and_builds() -> None:
    factory = EngineFactory(_registry())
    config = _config()
    assert factory.validate(config) == []
    engine = factory.build(config)
    assert isinstance(engine, ModeEngine)
    assert engine.config is config
    assert [p.kind for p in engine.partials] == ["artist"]


def test_unknown_keys_are_reported_with_paths() -> None:
    config = _config(
        source={"type": "marathon", "params": {}},
        scoring={"type": "party_speed", "params": {}},
        answer={
            "format": "classic",
            "matcher": {"type": "embeddings", "params": {}},
            "partial": ["artist", "franchise"],
        },
    )
    assert EngineFactory(_registry()).validate(config) == [
        ConfigIssue("source.type", UNKNOWN_PLUGIN),
        ConfigIssue("answer.matcher.type", UNKNOWN_PLUGIN),
        ConfigIssue("scoring.type", UNKNOWN_PLUGIN),
        ConfigIssue("answer.partial[1]", UNKNOWN_PLUGIN),
    ]


def test_build_refuses_unknown_clue() -> None:
    config = _config(clue={"type": "description", "params": {}})
    with pytest.raises(ConfigError) as info:
        EngineFactory(PluginRegistry()).build(config)
    assert info.value.code == "game.config_invalid"
    assert {"path": "clue.type", "code": UNKNOWN_PLUGIN} in info.value.details["errors"]
