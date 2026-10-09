"""Загрузка профилей уровней из YAML (``GV_MEDIA__PROFILES_PATH`` или встроенный файл)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from media_pipeline_service.application.generators import GeneratorRegistry
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.profiles import AssetProfileCatalog

_BUILTIN = "asset_profiles.yaml"


def read_profiles_document(path: Path | None = None) -> dict[str, Any]:
    """Прочитать YAML профилей (``safe_load``: без исполнения тегов)."""
    if path is None:
        text = (
            resources.files("media_pipeline_service.config").joinpath(_BUILTIN).read_text("utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise InvalidProfileError("asset_profiles: ожидается объект верхнего уровня")
    return document


def load_profiles(registry: GeneratorRegistry, path: Path | None = None) -> AssetProfileCatalog:
    """Каталог профилей; ошибка конфига — сразу при старте процесса."""
    return registry.build_catalog(read_profiles_document(path))
