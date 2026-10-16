"""Плагины генерации ассетов по ClueType."""

from media_pipeline_service.application.generators.base import (
    AssetGenerator,
    GeneratedAsset,
    GeneratorRegistry,
    SingleSourceGenerator,
)
from media_pipeline_service.application.generators.plugins import (
    AudioBlackoutGenerator,
    AudioIntroGenerator,
    AudioReverseGenerator,
    DescriptionGenerator,
    EmojiGenerator,
    FrameProgressiveGenerator,
    FrameSequenceGenerator,
    PosterMaskedGenerator,
    SoundtrackGenerator,
)

__all__ = [
    "AssetGenerator",
    "AudioBlackoutGenerator",
    "AudioIntroGenerator",
    "AudioReverseGenerator",
    "DescriptionGenerator",
    "EmojiGenerator",
    "FrameProgressiveGenerator",
    "FrameSequenceGenerator",
    "GeneratedAsset",
    "GeneratorRegistry",
    "PosterMaskedGenerator",
    "SingleSourceGenerator",
    "SoundtrackGenerator",
]
