"""Проекции чужих данных в game: единицы каталога и готовые наборы ассетов.

Строятся только из событий (``catalog.MediaItem*``, ``media_pipeline.Assets*``): чужую БД
game не читает. Порядок защищает ``version`` из payload — событие с версией не больше
применённой отбрасывается (повтор или перестановка при ретраях).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from gv_game_kernel import AssetRef, MediaItemRef, PersonRef


class ItemStatus(StrEnum):
    """Статус единицы каталога."""

    ACTIVE = "active"
    BLOCKED = "blocked"


class AssetStatus(StrEnum):
    """Статус набора ассетов под параметры загадки."""

    READY = "ready"
    FAILED = "failed"


def is_newer(incoming: int, current: int | None) -> bool:
    """Событие новее применённого (``current is None`` — проекции ещё нет)."""
    return current is None or incoming > current


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Единица каталога в проекции game.

    Attributes:
        media_item_id: Идентификатор единицы.
        domain: ``music`` | ``film``.
        kind: ``track`` | ``film``.
        status: ``active`` | ``blocked``.
        version: Последняя применённая версия события catalog.
        title: Основное название.
        year: Год.
        aliases: Нормализованные алиасы всех видов.
        title_aliases: Нормализованные алиасы видов ``title`` и ``alt`` (подмножество
            ``aliases``; для ``accept_same_title`` ядра).
        persons: Персоны.
        genres: Жанры.
        popularity: Популярность.
    """

    media_item_id: UUID
    domain: str
    kind: str
    status: ItemStatus
    version: int
    title: str
    year: int | None = None
    aliases: tuple[str, ...] = ()
    title_aliases: tuple[str, ...] = ()
    persons: tuple[PersonRef, ...] = ()
    genres: tuple[str, ...] = ()
    popularity: float | None = None

    @property
    def is_active(self) -> bool:
        """Единица не заблокирована."""
        return self.status is ItemStatus.ACTIVE

    def to_ref(self) -> MediaItemRef:
        """Ссылка для ядра движка."""
        return MediaItemRef(
            id=self.media_item_id,
            domain=self.domain,
            kind=self.kind,
            title=self.title,
            year=self.year,
            aliases=self.aliases,
            title_aliases=self.title_aliases,
            persons=self.persons,
            genres=self.genres,
            popularity=self.popularity,
        )


@dataclass(frozen=True, slots=True)
class ClueAssets:
    """Набор ассетов единицы под параметры загадки (ключ — ``params_hash``, D7).

    Attributes:
        media_item_id: Единица.
        clue_type: Тип загадки.
        params_hash: ``params_hash(clue.params)``.
        asset_set_id: Набор в media-pipeline.
        levels_count: Число уровней.
        status: ``ready`` | ``failed``.
        version: Версия события (монотонна по ``media_item_id``, ADR 0015).
    """

    media_item_id: UUID
    clue_type: str
    params_hash: str
    asset_set_id: UUID
    levels_count: int
    status: AssetStatus
    version: int

    @property
    def is_ready(self) -> bool:
        """Набор готов к выдаче ссылок."""
        return self.status is AssetStatus.READY

    def to_ref(self) -> AssetRef:
        """Ссылка для ядра движка."""
        return AssetRef(asset_set_id=self.asset_set_id, levels_count=self.levels_count)
