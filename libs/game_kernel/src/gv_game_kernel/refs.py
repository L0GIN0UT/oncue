"""Ссылки на внешние данные, которыми оперирует ядро: единица каталога и набор ассетов.

Ядро не знает, откуда они взялись: game строит их из своей проекции событий catalog и
media-pipeline. Значения неизменяемы.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PersonRef:
    """Персона единицы.

    Attributes:
        id: ``person_id`` каталога.
        name: Отображаемое имя.
        role: ``artist`` | ``director`` | ``actor`` (как в ``catalog.MediaItemCreated``).
    """

    id: UUID
    name: str
    role: str


@dataclass(frozen=True, slots=True)
class MediaItemRef:
    """Единица каталога, загаданная в раунде или выбранная игроком.

    Attributes:
        id: ``media_item_id``.
        domain: ``music`` | ``film``.
        kind: ``track`` | ``film``.
        title: Основное название (для ревила).
        year: Год выхода.
        aliases: Нормализованные алиасы (``aliases[].normalized`` каталога).
        persons: Персоны (исполнители, режиссёры, актёры).
        genres: Жанры.
        popularity: Популярность из метрик каталога.
    """

    id: UUID
    domain: str
    kind: str
    title: str
    year: int | None = None
    aliases: tuple[str, ...] = ()
    persons: tuple[PersonRef, ...] = ()
    genres: tuple[str, ...] = ()
    popularity: float | None = None


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Набор ассетов загадки, готовый под параметры режима (из ``AssetsReady``).

    Attributes:
        asset_set_id: Идентификатор набора в media-pipeline.
        levels_count: Число уровней в наборе.
    """

    asset_set_id: UUID
    levels_count: int
