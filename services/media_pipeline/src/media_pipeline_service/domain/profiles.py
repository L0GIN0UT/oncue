"""Профили уровней: какой набор ассетов строить для единиц какого домена (D5)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from media_pipeline_service.domain.clues import ClueParams
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.params import params_hash
from media_pipeline_service.domain.sources import SourceKind

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class AssetProfile:
    """Профиль: тип загадки + ключ профиля + проверенные параметры уровней.

    Attributes:
        clue_type: Ключ ClueType (``audio_intro``…).
        profile_key: Имя профиля внутри типа (``default``…).
        domains: Домены каталога, для которых профиль строится (``music``, ``film``).
        source_kind: Какой исходник нужен генератору.
        params: Проверенные параметры (модель конкретного типа загадки).
        params_hash: sha256 канонического JSON параметров.
    """

    clue_type: str
    profile_key: str
    domains: frozenset[str]
    source_kind: SourceKind
    params: ClueParams
    params_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name, value in (("clue_type", self.clue_type), ("profile_key", self.profile_key)):
            if not _KEY_RE.match(value):
                raise InvalidProfileError(f"{name} должен быть вида [a-z][a-z0-9_]*: {value!r}")
        if not self.domains:
            raise InvalidProfileError(f"{self.clue_type}/{self.profile_key}: пустой domains")
        object.__setattr__(self, "params_hash", params_hash(self.params_json()))

    @property
    def levels_count(self) -> int:
        """Число уровней профиля."""
        return self.params.levels_count

    def params_json(self) -> dict[str, Any]:
        """Параметры как JSON-объект (для хранения и хэша)."""
        data: dict[str, Any] = self.params.model_dump(mode="json")
        return data


class AssetProfileCatalog:
    """Набор профилей сервиса с поиском по домену и ключу."""

    def __init__(self, profiles: Iterable[AssetProfile]) -> None:
        self._by_key: dict[tuple[str, str], AssetProfile] = {}
        for profile in profiles:
            key = (profile.clue_type, profile.profile_key)
            if key in self._by_key:
                raise InvalidProfileError(f"профиль {key[0]}/{key[1]} объявлен дважды")
            self._by_key[key] = profile

    def __iter__(self) -> Any:
        return iter(self._by_key.values())

    def __len__(self) -> int:
        return len(self._by_key)

    def for_domain(self, domain: str) -> list[AssetProfile]:
        """Профили, применимые к домену единицы."""
        return [p for p in self._by_key.values() if domain in p.domains]

    def get(self, clue_type: str, profile_key: str) -> AssetProfile | None:
        """Профиль по ключу."""
        return self._by_key.get((clue_type, profile_key))


def profile_entries(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Список записей профилей из документа конфига ``{version: 1, profiles: [...]}``."""
    if document.get("version") != 1:
        raise InvalidProfileError("asset_profiles: поддерживается только version: 1")
    entries = document.get("profiles")
    if not isinstance(entries, list) or not entries:
        raise InvalidProfileError("asset_profiles: profiles должен быть непустым списком")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise InvalidProfileError("asset_profiles: запись профиля должна быть объектом")
    return entries
