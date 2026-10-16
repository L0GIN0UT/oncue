"""Проекции чужих данных в game: единицы каталога и готовые наборы ассетов.

Строятся только из событий (``catalog.MediaItem*``, ``media_pipeline.Assets*``): чужую БД
game не читает. Порядок защищает ``version`` из payload — событие с версией не больше
применённой отбрасывается (повтор или перестановка при ретраях).
"""

from __future__ import annotations

from collections.abc import Mapping
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
        metrics: Метрики каталога парами ``(имя, значение)`` (``vote_average``,
            ``revenue_usd``, ``runtime_min``, ``popularity``…) плюс ``year`` единицы —
            для «больше/меньше» (D9).
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
    metrics: tuple[tuple[str, float], ...] = ()

    @property
    def person_ids(self) -> tuple[UUID, ...]:
        """Персоны единицы без повторов (колонка ``person_ids`` с GIN-индексом, D14)."""
        return tuple(dict.fromkeys(p.id for p in self.persons))

    def metric(self, name: str) -> float | None:
        """Значение метрики ``name`` или ``None``."""
        return dict(self.metrics).get(name)

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
            metrics=self.metrics,
        )


def catalog_metrics(
    raw: Mapping[str, object] | None, *, year: int | None
) -> tuple[tuple[str, float], ...]:
    """Метрики события catalog → пары проекции.

    Берутся только числовые значения (неизвестные метрики тоже — список задаёт конфиг
    режима); год единицы добавляется метрикой ``year``: в событиях catalog её нет, а пара
    по году (``metric: year``) иначе была бы пустой.
    """
    values: dict[str, float] = {}
    for name, value in (raw or {}).items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            values[str(name)] = float(value)
    if year is not None:
        values["year"] = float(year)
    return tuple(sorted(values.items()))


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
