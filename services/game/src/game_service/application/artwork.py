"""Картинка ответа (обложка, постер) в ревиле: какой набор media-pipeline искать (ADR 0073)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gv_game_kernel.params import params_hash

ARTWORK_CLUE_TYPE = "artwork"


class ArtworkProfiles:
    """Профили ``artwork`` по домену: ``params_hash`` набора в проекции ``clue_assets``.

    Параметры берутся из настроек game и должны совпадать с профилями media-pipeline
    (хэш — от параметров как записаны; совпадение проверяет contract-тест по golden).

    Args:
        profiles: Домен -> параметры профиля.
    """

    def __init__(self, profiles: Mapping[str, Mapping[str, Any]]) -> None:
        self._hashes = {domain: params_hash(params) for domain, params in profiles.items()}

    def params_hash(self, domain: str) -> str | None:
        """Хэш профиля домена или ``None`` — картинка ответа для домена не настроена."""
        return self._hashes.get(domain)
