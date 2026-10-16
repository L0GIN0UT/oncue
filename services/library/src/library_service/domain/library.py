"""Медиатека пользователя — агрегат ``UserLibrary`` (D11–D12, ADR 0031).

Агрегат загружается под блокировкой строки ``user_versions`` пользователя: все мутации его
медиатеки идут по очереди, поэтому лимиты (полок, единиц на полке) и монотонная ``version``
событий не требуют дополнительных блокировок. Загружаются все полки пользователя (их не
больше ``custom_shelves_max`` + системные) и только нужные строки ``shelf_items``: строки
одной единицы (сохранение, удаление) или одной полки (удаление полки).

Изменения агрегат накапливает списком ``changes`` — репозиторий применяет их по порядку;
события ``ItemSaved``/``ItemRemoved`` пишутся только при фактическом изменении.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.domain import AggregateRoot
from library_service.domain.errors import (
    DomainMismatchError,
    ItemUnavailableError,
    ShelfFullError,
    ShelfNotFoundError,
    ShelvesLimitError,
)
from library_service.domain.events import ItemRemoved, ItemSaved
from library_service.domain.shelves import (
    RemoveSource,
    SaveSource,
    ShelfCatalog,
    ShelfKind,
    ShelfRef,
    SystemShelfSpec,
)


class ItemStatus(StrEnum):
    """Статус единицы в проекции catalog."""

    ACTIVE = "active"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PersonEntry:
    """Персона единицы из проекции catalog."""

    name: str
    role: str
    order: int


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Единица каталога в проекции медиатеки (``catalog_items_proj``).

    Attributes:
        version: Версия агрегата catalog: событие не новее применённого отбрасывается.
    """

    media_item_id: UUID
    domain: str
    kind: str
    status: ItemStatus
    title: str
    year: int | None
    persons: tuple[PersonEntry, ...]
    version: int

    @property
    def available(self) -> bool:
        """Можно ли сохранить единицу и показывать её доступной."""
        return self.status is ItemStatus.ACTIVE

    def card_persons(self, roles: tuple[str, ...], limit: int) -> list[str]:
        """Имена персон карточки: роли по приоритету конфига, внутри роли — по ``order``."""
        rank = {role: i for i, role in enumerate(roles)}
        chosen = sorted(
            (p for p in self.persons if p.role in rank), key=lambda p: (rank[p.role], p.order)
        )
        return list(dict.fromkeys(p.name for p in chosen))[:limit]


def is_newer(version: int, current: int | None) -> bool:
    """Событие с ``version`` новее применённого (повторы и перестановки отбрасываются)."""
    return current is None or version > current


@dataclass(slots=True)
class Shelf:
    """Полка пользователя (строка ``shelves``)."""

    id: UUID
    user_id: UUID
    kind: ShelfKind
    key: str | None
    title: str | None
    items_count: int
    created_at: datetime
    updated_at: datetime

    @property
    def ref(self) -> ShelfRef:
        """Ссылка на полку в API."""
        if self.kind is ShelfKind.SYSTEM:
            assert self.key is not None  # noqa: S101 — инвариант схемы (CHECK shelves)
            return ShelfRef.system(self.key)
        return ShelfRef(shelf_id=self.id)


@dataclass(frozen=True, slots=True)
class ShelfItem:
    """Единица на полке (строка ``shelf_items``); ``domain`` — на момент сохранения."""

    shelf_id: UUID
    media_item_id: UUID
    domain: str
    source: str
    added_at: datetime


@dataclass(frozen=True, slots=True)
class LibraryLimits:
    """Лимиты медиатеки (``GV_LIBRARY__LIMITS__*``)."""

    custom_shelves_max: int
    items_per_shelf_max: int


# ----------------------------------------------------------------- изменения для репозитория
@dataclass(frozen=True, slots=True)
class ShelfCreated:
    """Новая полка (системная лениво или своя)."""

    shelf: Shelf


@dataclass(frozen=True, slots=True)
class ShelfDeleted:
    """Своя полка удалена (её строки ``shelf_items`` — отдельными ``ItemDeleted``)."""

    shelf_id: UUID


@dataclass(frozen=True, slots=True)
class ItemAdded:
    """Единица положена на полку."""

    item: ShelfItem


@dataclass(frozen=True, slots=True)
class ItemDeleted:
    """Единица убрана с полки."""

    shelf_id: UUID
    media_item_id: UUID


type Change = ShelfCreated | ShelfDeleted | ItemAdded | ItemDeleted


@dataclass(frozen=True, slots=True)
class PlacementResult:
    """Итог сохранения или удаления единицы.

    Attributes:
        changed: Было ли фактическое изменение (иначе — повтор, без событий).
        shelves: Полки с единицей после операции (в порядке показа).
        removed_from: Полки, откуда единицу убрало ``exclusive_with``.
    """

    media_item_id: UUID
    changed: bool
    shelves: tuple[ShelfRef, ...]
    removed_from: tuple[ShelfRef, ...] = ()


class UserLibrary(AggregateRoot[UUID]):
    """Медиатека пользователя: полки и загруженные строки их единиц.

    Args:
        user_id: Владелец (он же id агрегата и ключ партиции событий).
        version: Текущая версия медиатеки (``user_versions``).
        shelves: Все полки пользователя.
        items: Загруженные строки ``shelf_items`` (одной единицы или одной полки).
        catalog: Конфиг полок.
        limits: Лимиты.
    """

    def __init__(
        self,
        user_id: UUID,
        version: int,
        shelves: Iterable[Shelf],
        items: Iterable[ShelfItem],
        *,
        catalog: ShelfCatalog,
        limits: LibraryLimits,
    ) -> None:
        super().__init__(user_id, version)
        self.user_id = user_id
        self.catalog = catalog
        self.limits = limits
        self.shelves: dict[UUID, Shelf] = {s.id: s for s in shelves}
        self.items: dict[tuple[UUID, UUID], ShelfItem] = {
            (i.shelf_id, i.media_item_id): i for i in items
        }
        self.changes: list[Change] = []
        self.touched: set[UUID] = set()
        """Полки, у которых изменились ``items_count``/``updated_at``/``title``."""

    # ------------------------------------------------------------------ чтение
    def by_key(self, key: str) -> Shelf | None:
        """Созданная системная полка по ключу."""
        return next(
            (s for s in self.shelves.values() if s.kind is ShelfKind.SYSTEM and s.key == key),
            None,
        )

    def custom(self, shelf_id: UUID) -> Shelf:
        """Своя полка пользователя.

        Raises:
            ShelfNotFoundError: нет такой своей полки (в том числе id системной).
        """
        shelf = self.shelves.get(shelf_id)
        if shelf is None or shelf.kind is not ShelfKind.CUSTOM:
            raise ShelfNotFoundError
        return shelf

    def resolve(self, ref: ShelfRef) -> tuple[Shelf | None, SystemShelfSpec | None]:
        """Полка по ссылке: ``(полка или None, если системная ещё не создана; spec)``.

        Raises:
            ShelfNotFoundError: ключа нет в конфиге или своей полки нет у пользователя.
        """
        if ref.key is not None:
            spec = self.catalog.spec(ref.key)
            return self.by_key(ref.key), spec
        assert ref.shelf_id is not None  # noqa: S101 — инвариант ShelfRef
        return self.custom(ref.shelf_id), None

    def domains_of(self, spec: SystemShelfSpec | None) -> tuple[str, ...]:
        """Домены полки: системной — из её spec, своей — ``custom.domains`` конфига."""
        return spec.domains if spec is not None else self.catalog.custom_domains

    def visible(self) -> list[Shelf]:
        """Полки в порядке показа: системные (по конфигу, без исчезнувших из конфига), свои."""
        order = {s.key: i for i, s in enumerate(self.catalog.system)}
        system = sorted(
            (s for s in self.shelves.values() if s.kind is ShelfKind.SYSTEM and s.key in order),
            key=lambda s: order[s.key or ""],
        )
        custom = sorted(
            (s for s in self.shelves.values() if s.kind is ShelfKind.CUSTOM),
            key=lambda s: (s.created_at, s.id),
        )
        return system + custom

    def refs_with(self, media_item_id: UUID) -> tuple[ShelfRef, ...]:
        """Полки (в порядке показа), на которых лежит единица."""
        return tuple(s.ref for s in self.visible() if (s.id, media_item_id) in self.items)

    # ------------------------------------------------------------------ единицы
    def save(
        self,
        ref: ShelfRef,
        entry: CatalogEntry | None,
        source: SaveSource,
        *,
        now: datetime,
        new_shelf_id: UUID,
    ) -> PlacementResult:
        """Сохранить единицу на полку; системная полка создаётся лениво.

        Повтор — без изменений и событий. Системная полка с ``exclusive_with`` убирает
        единицу с исключающих полок (``ItemRemoved`` с ``source: exclusive``).

        Raises:
            ShelfNotFoundError: полки нет.
            ItemUnavailableError: единица неизвестна или заблокирована.
            DomainMismatchError: домен единицы не из доменов полки.
            ShelfFullError: на полке ``items_per_shelf_max`` единиц.
        """
        shelf, spec = self.resolve(ref)
        if entry is None or not entry.available:
            raise ItemUnavailableError
        allowed = self.domains_of(spec)
        if entry.domain not in allowed:
            raise DomainMismatchError(entry.domain, allowed)
        if shelf is None:
            assert spec is not None  # noqa: S101 — своя полка не бывает «не создана»
            shelf = self._create(ShelfKind.SYSTEM, spec.key, None, new_shelf_id, now)
        item_id = entry.media_item_id
        changed = False
        if (shelf.id, item_id) not in self.items:
            if shelf.items_count >= self.limits.items_per_shelf_max:
                raise ShelfFullError(self.limits.items_per_shelf_max)
            self._add(shelf, ShelfItem(shelf.id, item_id, entry.domain, str(source), now))
            changed = True
        removed: list[ShelfRef] = []
        for key in spec.exclusive_with if spec is not None else ():
            other = self.by_key(key)
            if other is not None and (other.id, item_id) in self.items:
                self._remove(other, item_id, RemoveSource.EXCLUSIVE, now)
                removed.append(other.ref)
        return PlacementResult(
            item_id, changed or bool(removed), self.refs_with(item_id), tuple(removed)
        )

    def remove(
        self, ref: ShelfRef, media_item_id: UUID, source: SaveSource, *, now: datetime
    ) -> PlacementResult:
        """Убрать единицу с полки; повтор (или несозданная системная полка) — без событий.

        Raises:
            ShelfNotFoundError: полки нет.
        """
        shelf, _ = self.resolve(ref)
        changed = False
        if shelf is not None and (shelf.id, media_item_id) in self.items:
            self._remove(shelf, media_item_id, RemoveSource(source.value), now)
            changed = True
        return PlacementResult(media_item_id, changed, self.refs_with(media_item_id))

    # ------------------------------------------------------------------ свои полки
    def create_shelf(self, title: str, *, now: datetime, new_shelf_id: UUID) -> Shelf:
        """Своя полка (название уже проверено ``gv_text.ugc``).

        Raises:
            ShelvesLimitError: своих полок уже ``custom_shelves_max``.
        """
        count = sum(1 for s in self.shelves.values() if s.kind is ShelfKind.CUSTOM)
        if count >= self.limits.custom_shelves_max:
            raise ShelvesLimitError(self.limits.custom_shelves_max)
        return self._create(ShelfKind.CUSTOM, None, title, new_shelf_id, now)

    def rename_shelf(self, shelf_id: UUID, title: str, *, now: datetime) -> Shelf:
        """Переименовать свою полку; то же название — без изменений.

        Raises:
            ShelfNotFoundError: нет такой своей полки.
        """
        shelf = self.custom(shelf_id)
        if shelf.title != title:
            shelf.title = title
            shelf.updated_at = now
            self.touched.add(shelf.id)
        return shelf

    def delete_shelf(self, shelf_id: UUID, *, now: datetime) -> int:
        """Удалить свою полку; её единицы — ``ItemRemoved`` с ``source: shelf_deleted``.

        Строки единиц полки должны быть загружены в агрегат целиком.

        Returns:
            Число убранных единиц.

        Raises:
            ShelfNotFoundError: нет такой своей полки.
        """
        shelf = self.custom(shelf_id)
        on_shelf = sorted(
            (i for i in self.items.values() if i.shelf_id == shelf.id),
            key=lambda i: (i.added_at, i.media_item_id),
        )
        for item in on_shelf:
            self._remove(shelf, item.media_item_id, RemoveSource.SHELF_DELETED, now)
        del self.shelves[shelf.id]
        self.touched.discard(shelf.id)
        self.changes.append(ShelfDeleted(shelf.id))
        return len(on_shelf)

    # ------------------------------------------------------------------ внутреннее
    def _next_version(self) -> int:
        self.version += 1
        return self.version

    def _create(
        self, kind: ShelfKind, key: str | None, title: str | None, shelf_id: UUID, now: datetime
    ) -> Shelf:
        shelf = Shelf(shelf_id, self.user_id, kind, key, title, 0, now, now)
        self.shelves[shelf.id] = shelf
        self.changes.append(ShelfCreated(shelf))
        return shelf

    def _event[E: (ItemSaved, ItemRemoved)](
        self,
        cls: type[E],
        shelf: Shelf,
        item: ShelfItem,
        *,
        source: str,
        now: datetime,
    ) -> E:
        return cls(
            aggregate_id=str(self.user_id),
            user_id=self.user_id,
            shelf_id=shelf.id,
            shelf_kind=str(shelf.kind),
            shelf_key=shelf.key,
            media_item_id=item.media_item_id,
            domain=item.domain,
            source=source,
            version=self._next_version(),
            at=now,
        )

    def _add(self, shelf: Shelf, item: ShelfItem) -> None:
        self.items[(shelf.id, item.media_item_id)] = item
        shelf.items_count += 1
        shelf.updated_at = item.added_at
        self.touched.add(shelf.id)
        self.changes.append(ItemAdded(item))
        self.record(self._event(ItemSaved, shelf, item, source=item.source, now=item.added_at))

    def _remove(
        self, shelf: Shelf, media_item_id: UUID, source: RemoveSource, now: datetime
    ) -> None:
        item = self.items.pop((shelf.id, media_item_id))
        shelf.items_count -= 1
        shelf.updated_at = now
        self.touched.add(shelf.id)
        self.changes.append(ItemDeleted(shelf.id, media_item_id))
        self.record(self._event(ItemRemoved, shelf, item, source=str(source), now=now))
