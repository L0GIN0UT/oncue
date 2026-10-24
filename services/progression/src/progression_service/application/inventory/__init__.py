"""Инвентарь игрока и ленивое открытие предметов по уровню (ADR 0045).

Открытие по уровню идёт без проводки: при чтении витрины и инвентаря и при оформлении
выполняется ``INSERT … ON CONFLICT DO NOTHING`` для активных предметов с
``level_required <= текущий уровень`` (``source = level``). Свежеактивированный предмет
достаётся игрокам при их следующем чтении, фонового прохода по всем игрокам нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gv_core.clock import Clock
from progression_service.application.ports import ProgressionUnitOfWork, UnitOfWorkFactory
from progression_service.domain.config import ShopConfig
from progression_service.domain.inventory import InventoryItem
from progression_service.domain.shop import Item


async def shop_config(uow: ProgressionUnitOfWork) -> ShopConfig | None:
    """Секция ``shop`` действующего конфига; нет конфига или секции - магазин пуст."""
    published = await uow.configs.published()
    return None if published is None else published.config.shop


async def user_level(uow: ProgressionUnitOfWork, user_id: UUID) -> int:
    """Уровень игрока; нет прогресса - первый."""
    row = await uow.progress.get(user_id)
    return 1 if row is None else row.level


async def unlock_by_level(
    uow: ProgressionUnitOfWork, user_id: UUID, now: datetime, *, level: int | None = None
) -> int:
    """Выдать предметы, открывшиеся уровнем игрока. Повторный вызов ничего не меняет.

    Returns:
        Сколько предметов выдано сейчас.
    """
    current = level if level is not None else await user_level(uow, user_id)
    return await uow.inventory.grant_level_items(user_id, current, now)


@dataclass(frozen=True, slots=True)
class InventoryEntryView:
    """Предмет инвентаря с признаком «надет»."""

    item: Item
    entry: InventoryItem
    equipped: bool


class InventoryService:
    """Чтение инвентаря."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def mine(self, user_id: UUID) -> list[InventoryEntryView]:
        """Непросроченные предметы игрока; сначала открывается всё, что дал уровень."""
        async with self._uow() as uow:
            now = self._clock.now()
            await unlock_by_level(uow, user_id, now)
            entries = await uow.inventory.entries(user_id, now)
            items = await uow.items.by_ids([e.item_id for e in entries])
            worn = set((await uow.equipped.mine(user_id)).values())
            await uow.commit()
        return [
            InventoryEntryView(items[e.item_id], e, e.item_id in worn)
            for e in entries
            if e.item_id in items
        ]
