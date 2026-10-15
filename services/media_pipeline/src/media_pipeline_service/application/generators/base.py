"""Плагины генерации ассетов: протокол и реестр по ключу ClueType.

Новый тип загадки = новый класс-генератор + ``registry.register(...)`` в composition root.
Ни use-cases, ни репозитории не ветвятся по типу загадки (CLAUDE.md, правило 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from pydantic import ValidationError

from media_pipeline_service.domain.clues import ClueParams
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.profiles import (
    AssetProfile,
    AssetProfileCatalog,
    ProfileOrigin,
    profile_entries,
)
from media_pipeline_service.domain.sources import SourceKind, SourceMedia, best_source


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

    Генератор сам объявляет, какие исходники ему нужны (``source_kind``, ``source_count``,
    ``sources_for``): use-cases планирования и генерации не ветвятся по типу загадки.

    Attributes:
        clue_type: Ключ типа загадки.
        source_kind: Вид исходников.
        params_model: Модель параметров уровней (повторяет ``contracts/clues``).
    """

    clue_type: ClassVar[str]
    source_kind: ClassVar[SourceKind]
    params_model: ClassVar[type[ClueParams]]

    def source_count(self, params: ClueParams) -> int:
        """Сколько исходников вида ``source_kind`` нужно набору с такими параметрами."""
        ...

    def sources_for(
        self, params: ClueParams, sources: Sequence[SourceMedia]
    ) -> list[SourceMedia] | None:
        """Исходники набора в порядке, в котором их ждёт ``generate``.

        Returns:
            Ровно ``source_count(params)`` исходников или ``None`` — у единицы нет нужных
            (набор не планируется: единица не играбельна в этом профиле).
        """
        ...

    async def generate(self, sources: Sequence[bytes], params: ClueParams) -> list[GeneratedAsset]:
        """Сгенерировать объекты уровней 1..N (индекс списка + 1 = номер уровня).

        Args:
            sources: Содержимое исходников в порядке ``sources_for``.
        """
        ...


class SingleSourceGenerator:
    """Основа генераторов с одним исходником: лучший исходник вида ``source_kind``."""

    source_kind: ClassVar[SourceKind]

    def source_count(self, params: ClueParams) -> int:  # noqa: ARG002 — сигнатура протокола
        """Один исходник."""
        return 1

    def sources_for(
        self, params: ClueParams, sources: Sequence[SourceMedia]
    ) -> list[SourceMedia] | None:
        """Лучший исходник вида (загруженный админом, затем свежий)."""
        source = best_source(sources, self.source_kind)
        if source is None or not self.accepts(params, source):
            return None
        return [source]

    def accepts(self, params: ClueParams, source: SourceMedia) -> bool:  # noqa: ARG002
        """Подходит ли исходник под параметры (по умолчанию — любой своего вида)."""
        return True


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
            # Хэш — от параметров в том виде, как они записаны (как его считает game по
            # конфигу режима): поля по умолчанию, добавленные моделью, его не меняют.
            raw_params=dict(params),
        )

    def build_catalog(self, document: Mapping[str, Any]) -> AssetProfileCatalog:
        """Каталог профилей из документа ``asset_profiles.yaml``."""
        return AssetProfileCatalog(self.build_profile(e) for e in profile_entries(document))

    def build_dynamic(
        self, *, clue_type: str, profile_key: str, domain: str, params: Mapping[str, Any]
    ) -> AssetProfile:
        """Динамический профиль (ADR 0025): параметры проверяет модель генератора, но хранятся
        и хэшируются они в присланном виде.

        Raises:
            InvalidProfileError: неизвестный ``clue_type`` или параметры не проходят модель.
        """
        generator = self.get(clue_type)
        if generator is None:
            raise InvalidProfileError(
                f"неизвестный clue_type: {clue_type!r}", code="profile.unknown_clue_type"
            )
        try:
            parsed = generator.params_model.from_mapping(params)
        except (ValidationError, ValueError) as exc:
            raise InvalidProfileError(f"{clue_type}: неверные параметры уровней: {exc}") from exc
        return AssetProfile(
            clue_type=clue_type,
            profile_key=profile_key,
            domains=frozenset({domain}),
            source_kind=generator.source_kind,
            params=parsed,
            origin=ProfileOrigin.DYNAMIC,
            raw_params=dict(params),
        )

    def params_for(self, clue_type: str, params: Mapping[str, Any]) -> ClueParams:
        """Разобрать сохранённые параметры набора (при генерации)."""
        generator = self.get(clue_type)
        if generator is None:
            raise InvalidProfileError(f"неизвестный clue_type: {clue_type!r}")
        return generator.params_model.from_mapping(params)
