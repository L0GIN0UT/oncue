"""Встроенные плагины движка и их регистрация.

``register_builtin(registry)`` регистрирует всё, что game оркестрирует на этапе 2:
загадки ``audio_intro``, ``frame_progressive``, ``poster_masked``; источники ``daily`` и
``random``; matcher ``title_alias``; частичный ответ ``artist``; очки ``by_level`` и
``by_speed``. Источник ``collection`` готов, но регистрируется отдельно
(``register_collection``) — в game с этапа 3, вместе с проекциями сборников.
"""

from __future__ import annotations

from gv_game_kernel.builtin.clues import (
    AudioIntroClue,
    AudioIntroParams,
    FrameProgressiveClue,
    FrameProgressiveParams,
    LeveledClue,
    PosterMaskedClue,
    PosterMaskedParams,
)
from gv_game_kernel.builtin.matching import (
    ArtistPartial,
    TitleAliasMatcher,
    TitleAliasParams,
    TypoRule,
)
from gv_game_kernel.builtin.pool import PoolFilters, PoolParams, YearRange
from gv_game_kernel.builtin.scoring import (
    ByLevelParams,
    ByLevelScorer,
    BySpeedParams,
    BySpeedScorer,
)
from gv_game_kernel.builtin.sources import (
    CollectionParams,
    CollectionSource,
    DailyParams,
    DailySource,
    PresetParams,
    RandomParams,
    RandomSource,
    daily_seed,
)
from gv_game_kernel.registry import PluginRegistry


def register_builtin(registry: PluginRegistry) -> PluginRegistry:
    """Зарегистрировать встроенные плагины этапа 2 (без ``collection``).

    Returns:
        Тот же реестр (для цепочки ``register_builtin(PluginRegistry())``).

    Raises:
        DuplicatePluginError: какой-то ключ уже зарегистрирован.
    """
    for clue in (AudioIntroClue(), FrameProgressiveClue(), PosterMaskedClue()):
        registry.register_clue(clue)
    registry.register_source(DailySource())
    registry.register_source(RandomSource())
    registry.register_matcher(TitleAliasMatcher())
    registry.register_partial(ArtistPartial())
    registry.register_scorer(ByLevelScorer())
    registry.register_scorer(BySpeedScorer())
    return registry


def register_collection(registry: PluginRegistry) -> PluginRegistry:
    """Зарегистрировать источник ``collection`` (нужен ``DrawContext.collections``)."""
    registry.register_source(CollectionSource())
    return registry


__all__ = [
    "ArtistPartial",
    "AudioIntroClue",
    "AudioIntroParams",
    "ByLevelParams",
    "ByLevelScorer",
    "BySpeedParams",
    "BySpeedScorer",
    "CollectionParams",
    "CollectionSource",
    "DailyParams",
    "DailySource",
    "FrameProgressiveClue",
    "FrameProgressiveParams",
    "LeveledClue",
    "PoolFilters",
    "PoolParams",
    "PosterMaskedClue",
    "PosterMaskedParams",
    "PresetParams",
    "RandomParams",
    "RandomSource",
    "TitleAliasMatcher",
    "TitleAliasParams",
    "TypoRule",
    "YearRange",
    "daily_seed",
    "register_builtin",
    "register_collection",
]
