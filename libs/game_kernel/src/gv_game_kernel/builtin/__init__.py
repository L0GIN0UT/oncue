"""Встроенные плагины движка и их регистрация.

``register_builtin(registry)`` регистрирует всё, что game оркестрирует:

* загадки ``audio_intro``, ``frame_progressive``, ``poster_masked`` (этап 2) и
  ``audio_reverse``, ``soundtrack``, ``audio_blackout``, ``frame_sequence``,
  ``description``, ``emoji`` (этап 3a);
* источники ``daily``, ``random`` и ``marathon``, ``pair``, ``bracket``;
* matcher-ы ``title_alias`` и ``metric_compare``, ``preference``; частичный ответ ``artist``;
* очки ``by_level``, ``by_speed`` и ``streak``, ``none``;
* форматы ответа ``classic``, ``pick``; правила завершения ``all_rounds``, ``lives``.

Источник ``collection`` готов, но регистрируется отдельно (``register_collection``) — в
game вместе с проекциями сборников (этап 3b).
"""

from __future__ import annotations

from gv_game_kernel.builtin.answer_formats import ClassicFormat, PickFormat
from gv_game_kernel.builtin.clues import (
    AudioBlackoutClue,
    AudioBlackoutParams,
    AudioIntroClue,
    AudioIntroParams,
    AudioReverseClue,
    AudioReverseParams,
    DescriptionClue,
    DescriptionParams,
    EmojiClue,
    EmojiParams,
    FrameProgressiveClue,
    FrameProgressiveParams,
    FrameSequenceClue,
    FrameSequenceParams,
    LeveledClue,
    PosterMaskedClue,
    PosterMaskedParams,
    SoundtrackClue,
    SoundtrackParams,
)
from gv_game_kernel.builtin.end_rules import AllRoundsEnd, AllRoundsParams, LivesEnd, LivesParams
from gv_game_kernel.builtin.matching import (
    HIGHER,
    LOWER,
    ArtistPartial,
    MetricCompareMatcher,
    MetricCompareParams,
    PreferenceMatcher,
    PreferenceParams,
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
    NoneParams,
    NoneScorer,
    StreakParams,
    StreakScorer,
)
from gv_game_kernel.builtin.sources import (
    BracketParams,
    BracketSource,
    CollectionParams,
    CollectionSource,
    DailyParams,
    DailySource,
    MarathonParams,
    MarathonSource,
    PairParams,
    PairSource,
    PresetParams,
    RandomParams,
    RandomSource,
    daily_seed,
)
from gv_game_kernel.registry import PluginRegistry


def register_builtin(registry: PluginRegistry) -> PluginRegistry:
    """Зарегистрировать встроенные плагины (без ``collection``).

    Returns:
        Тот же реестр (для цепочки ``register_builtin(PluginRegistry())``).

    Raises:
        DuplicatePluginError: какой-то ключ уже зарегистрирован.
    """
    for clue in (
        AudioIntroClue(),
        FrameProgressiveClue(),
        PosterMaskedClue(),
        AudioReverseClue(),
        SoundtrackClue(),
        AudioBlackoutClue(),
        FrameSequenceClue(),
        DescriptionClue(),
        EmojiClue(),
    ):
        registry.register_clue(clue)
    for source in (DailySource(), RandomSource(), MarathonSource(), PairSource(), BracketSource()):
        registry.register_source(source)
    for matcher in (TitleAliasMatcher(), MetricCompareMatcher(), PreferenceMatcher()):
        registry.register_matcher(matcher)
    registry.register_partial(ArtistPartial())
    for scorer in (ByLevelScorer(), BySpeedScorer(), StreakScorer(), NoneScorer()):
        registry.register_scorer(scorer)
    registry.register_answer_format(ClassicFormat())
    registry.register_answer_format(PickFormat())
    registry.register_end_rule(AllRoundsEnd())
    registry.register_end_rule(LivesEnd())
    return registry


def register_collection(registry: PluginRegistry) -> PluginRegistry:
    """Зарегистрировать источник ``collection`` (нужен ``DrawContext.collections``)."""
    registry.register_source(CollectionSource())
    return registry


__all__ = [
    "HIGHER",
    "LOWER",
    "AllRoundsEnd",
    "AllRoundsParams",
    "ArtistPartial",
    "AudioBlackoutClue",
    "AudioBlackoutParams",
    "AudioIntroClue",
    "AudioIntroParams",
    "AudioReverseClue",
    "AudioReverseParams",
    "BracketParams",
    "BracketSource",
    "ByLevelParams",
    "ByLevelScorer",
    "BySpeedParams",
    "BySpeedScorer",
    "ClassicFormat",
    "CollectionParams",
    "CollectionSource",
    "DailyParams",
    "DailySource",
    "DescriptionClue",
    "DescriptionParams",
    "EmojiClue",
    "EmojiParams",
    "FrameProgressiveClue",
    "FrameProgressiveParams",
    "FrameSequenceClue",
    "FrameSequenceParams",
    "LeveledClue",
    "LivesEnd",
    "LivesParams",
    "MarathonParams",
    "MarathonSource",
    "MetricCompareMatcher",
    "MetricCompareParams",
    "NoneParams",
    "NoneScorer",
    "PairParams",
    "PairSource",
    "PickFormat",
    "PoolFilters",
    "PoolParams",
    "PosterMaskedClue",
    "PosterMaskedParams",
    "PreferenceMatcher",
    "PreferenceParams",
    "PresetParams",
    "RandomParams",
    "RandomSource",
    "SoundtrackClue",
    "SoundtrackParams",
    "StreakParams",
    "StreakScorer",
    "TitleAliasMatcher",
    "TitleAliasParams",
    "TypoRule",
    "YearRange",
    "daily_seed",
    "register_builtin",
    "register_collection",
]
