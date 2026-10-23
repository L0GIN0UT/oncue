"""Оформление профиля: по одному предмету на слот (ADR 0045)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from gv_core.errors import InvalidInputError, NotFoundError
from progression_service.domain.shop import Item

BATCH_MAX = 12
"""Сколько игроков за вызов отдаёт публичное оформление (то же ``maxItems`` в OpenAPI)."""


class SlotNotFoundError(NotFoundError):
    """Слота нет в секции ``shop.slots`` конфига (404)."""

    default_code = "progression.slot_not_found"

    def __init__(self) -> None:
        super().__init__("слот не найден")


class ItemNotOwnedError(NotFoundError):
    """Предмета нет в инвентаре игрока или нет вовсе (404, ответ одинаков)."""

    default_code = "progression.item_not_owned"

    def __init__(self) -> None:
        super().__init__("предмета нет в инвентаре")


class ItemSlotMismatchError(InvalidInputError):
    """Предмет не подходит этому слоту (422)."""

    default_code = "progression.item_slot_mismatch"

    def __init__(self) -> None:
        super().__init__("предмет не подходит этому слоту")


def check_equip(
    slots: Collection[str],
    slot: str,
    item: Item | None,
    *,
    owned: bool,
    item_id: UUID | None = None,
) -> None:
    """Можно ли надеть ``item`` в ``slot`` (или снять, если ``item`` и ``item_id`` пусты).

    Args:
        slots: Ключи слотов из конфига.
        slot: Целевой слот.
        item: Предмет, если он существует.
        owned: Предмет в инвентаре игрока.
        item_id: Запрошенный id (если предмета нет в БД, ответ тот же, что «не ваш»).

    Raises:
        SlotNotFoundError: слота нет в конфиге.
        ItemNotOwnedError: предмета нет у игрока.
        ItemSlotMismatchError: слот предмета другой.
    """
    if slot not in slots:
        raise SlotNotFoundError
    if item is None:
        if item_id is not None:
            raise ItemNotOwnedError
        return
    if not owned or not item.active:  # снятый с продажи предмет - как чужой
        raise ItemNotOwnedError
    if item.slot != slot:
        raise ItemSlotMismatchError


def unique_user_ids(ids: Sequence[UUID]) -> list[UUID]:
    """Дубли схлопываются, порядок первого появления сохраняется."""
    return list(dict.fromkeys(ids))
