"""Плагины генерации ассетов по ClueType."""

from media_pipeline_service.application.generators.base import (
    AssetGenerator,
    GeneratedAsset,
    GeneratorRegistry,
)
from media_pipeline_service.application.generators.plugins import (
    AudioIntroGenerator,
    FrameProgressiveGenerator,
    PosterMaskedGenerator,
)

__all__ = [
    "AssetGenerator",
    "AudioIntroGenerator",
    "FrameProgressiveGenerator",
    "GeneratedAsset",
    "GeneratorRegistry",
    "PosterMaskedGenerator",
]
