"""Плагины генерации ассетов: протокол и реестр по ключу ClueType.

Новый тип загадки = новый класс-генератор + ``registry.register(...)`` в composition root.
Ни use-cases, ни репозитории не ветвятся по типу загадки (CLAUDE.md, правило 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from pydantic import ValidationError

from media_pipeline_service.domain.clues import ClueParams
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.profiles import (
    AssetProfile,
    AssetProfileCatalog,
    profile_entries,
)
from media_pipeline_service.domain.sources import SourceKind


@dataclass(frozen=True, slots=True)
class GeneratedAsset:
    """Объект одного уровня, ещё не сохранённый в хранилище.

    Attributes:
        data: Содержимое (без метаданных).
        mime: MIME.
        ext: Расширение ключа объекта.
        duration_ms: Длительность (аудио).
        width: Ширина (изображения).
        height: Высота (изображения).
    """

    data: bytes
    mime: str
    ext: str
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None


class AssetGenerator(Protocol):
    """Генератор ассетов одного ClueType.

    Attributes:
        clue_type: Ключ типа загадки.
        source_kind: Какой исходник нужен.
        params_model: Модель параметров уровней (повторяет ``contracts/clues``).
    """

    clue_type: ClassVar[str]
    source_kind: ClassVar[SourceKind]
    params_model: ClassVar[type[ClueParams]]

    async def generate(self, source: bytes, params: ClueParams) -> list[GeneratedAsset]:
        """Сгенерировать объекты уровней 1..N (индекс списка + 1 = номер уровня)."""
        ...


class GeneratorRegistry:
    """Реестр генераторов по ``clue_type`` и сборка профилей из конфига."""

    def __init__(self, generators: Iterable[AssetGenerator] = ()) -> None:
        self._generators: dict[str, AssetGenerator] = {}
        for generator in generators:
            self.register(generator)

    def register(self, generator: AssetGenerator) -> None:
        """Зарегистрировать генератор. Повтор ключа — ошибка конфигурации."""
        if generator.clue_type in self._generators:
            raise ValueError(f"генератор {generator.clue_type} уже зарегистрирован")
        self._generators[generator.clue_type] = generator

    def get(self, clue_type: str) -> AssetGenerator | None:
        """Генератор по ключу или ``None``."""
        return self._generators.get(clue_type)

    @property
    def clue_types(self) -> frozenset[str]:
        """Зарегистрированные типы загадок."""
        return frozenset(self._generators)

    def build_profile(self, entry: Mapping[str, Any]) -> AssetProfile:
        """Профиль из записи конфига ``{clue_type, profile_key, domains, params}``."""
        clue_type = str(entry.get("clue_type", ""))
        generator = self.get(clue_type)
        if generator is None:
            raise InvalidProfileError(f"неизвестный clue_type в профиле: {clue_type!r}")
        unknown = set(entry) - {"clue_type", "profile_key", "domains", "params"}
        if unknown:
            raise InvalidProfileError(f"{clue_type}: лишние поля профиля {sorted(unknown)}")
        domains = entry.get("domains")
        params = entry.get("params")
        if not isinstance(domains, list) or not isinstance(params, Mapping):
            raise InvalidProfileError(f"{clue_type}: domains — список, params — объект")
        try:
            parsed = generator.params_model.from_mapping(params)
        except (ValidationError, ValueError) as exc:
            raise InvalidProfileError(f"{clue_type}: неверные параметры уровней: {exc}") from exc
        return AssetProfile(
            clue_type=clue_type,
            profile_key=str(entry.get("profile_key", "")),
            domains=frozenset(str(d) for d in domains),
            source_kind=generator.source_kind,
            params=parsed,
        )

    def build_catalog(self, document: Mapping[str, Any]) -> AssetProfileCatalog:
        """Каталог профилей из документа ``asset_profiles.yaml``."""
        return AssetProfileCatalog(self.build_profile(e) for e in profile_entries(document))

    def params_for(self, clue_type: str, params: Mapping[str, Any]) -> ClueParams:
        """Разобрать сохранённые параметры набора (при генерации)."""
        generator = self.get(clue_type)
        if generator is None:
            raise InvalidProfileError(f"неизвестный clue_type: {clue_type!r}")
        return generator.params_model.from_mapping(params)
