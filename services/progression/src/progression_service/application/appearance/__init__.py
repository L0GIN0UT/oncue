"""Оформление профиля: свой набор, смена слота, публичный пакет для матча (ADR 0045)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gv_core.clock import Clock
from progression_service.application.accrual.posting import Poster
from progression_service.application.events.progress import apply_metric_updates
from progression_service.application.inventory import shop_config, unlock_by_level
from progression_service.application.ports import ProgressionUnitOfWork, UnitOfWorkFactory
from progression_service.domain import appearance, metrics
from progression_service.domain.events import ItemEquipped
from progression_service.domain.metrics import MetricUpdate
from progression_service.domain.shop import Item


@dataclass(frozen=True, slots=True)
class SlotView:
    """Слот оформления: ключ и надетый предмет (``None`` - пусто)."""

    slot: str
    item: Item | None


@dataclass(frozen=True, slots=True)
class PlayerView:
    """Оформление игрока."""

    user_id: UUID
    slots: list[SlotView]
    custom_avatar_key: str | None = None  # u/<hex>.webp действующей своей аватарки


async def _views(
    uow: ProgressionUnitOfWork, slots: list[str], worn: dict[str, UUID]
) -> list[SlotView]:
    items = await uow.items.by_ids(set(worn.values()))
    # Снятый с продажи предмет в профиле и в матче не показываем: слот выглядит пустым.
    visible = {i: item for i, item in items.items() if item.active}
    return [SlotView(s, visible.get(worn[s]) if s in worn else None) for s in slots]


class AppearanceService:
    """Оформление игроков."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def mine(self, user_id: UUID) -> list[SlotView]:
        """Слоты игрока в порядке конфига."""
        async with self._uow() as uow:
            cfg = await shop_config(uow)
            slots = [s.key for s in cfg.slots] if cfg else []
            return await _views(uow, slots, await uow.equipped.mine(user_id))

    async def equip(self, user_id: UUID, slot: str, item_id: UUID | None) -> list[SlotView]:
        """Надеть предмет в слот или снять (``item_id = None``).

        Событие ``ItemEquipped`` публикуется только при реальной смене слота.

        Raises:
            SlotNotFoundError: слота нет в конфиге (404).
            ItemNotOwnedError: предмета нет у игрока (404).
            ItemSlotMismatchError: предмет другого слота (422).
        """
        async with self._uow() as uow:
            now = self._clock.now()
            cfg = await shop_config(uow)
            slots = [s.key for s in cfg.slots] if cfg else []
            await unlock_by_level(uow, user_id, now)
            item = None if item_id is None else await uow.items.get(item_id)
            owned = item is not None and await uow.inventory.get(user_id, item.id, now) is not None
            appearance.check_equip(slots, slot, item, owned=owned, item_id=item_id)
            worn = await uow.equipped.mine(user_id)
            if item is None:
                if slot in worn:
                    await uow.equipped.clear(user_id, slot)
                    del worn[slot]
                    self._publish(uow, user_id, slot, None, now)
            elif worn.get(slot) != item.id:
                await uow.equipped.put(user_id, slot, item.id, now)
                worn[slot] = item.id
                self._publish(uow, user_id, slot, item, now)
                await self._event_progress(uow, user_id, slot, item, now)
            views = await _views(uow, slots, worn)
            await uow.commit()
        return views

    @staticmethod
    async def _event_progress(
        uow: ProgressionUnitOfWork, user_id: UUID, slot: str, item: Item, now: datetime
    ) -> None:
        """Надетый предмет двигает находки событий ``item_equipped`` (ADR 0055)."""
        published = await uow.configs.published()
        if published is None:
            return
        update = MetricUpdate(metrics.ITEM_EQUIPPED, 1, slot=slot, item_key=item.key)
        poster = Poster(uow, published, now)
        await apply_metric_updates(uow, published, poster, user_id, now, [update], now)

    @staticmethod
    def _publish(
        uow: ProgressionUnitOfWork, user_id: UUID, slot: str, item: Item | None, now: datetime
    ) -> None:
        uow.publish(
            ItemEquipped(
                aggregate_id=str(user_id),
                user_id=user_id,
                slot=slot,
                item_id=None if item is None else item.id,
                item_key=None if item is None else item.key,
                at=now,
            )
        )

    async def players(self, user_ids: Collection[UUID]) -> list[PlayerView]:
        """Публичное оформление игроков; неизвестные (без прогресса) пропускаются."""
        unique = appearance.unique_user_ids(list(user_ids))
        async with self._uow() as uow:
            cfg = await shop_config(uow)
            slots = [s.key for s in cfg.slots] if cfg else []
            known = [u for u in unique if await uow.progress.get(u) is not None]
            worn = await uow.equipped.many(known)
            avatars = await uow.avatars.currents(known)
            return [
                PlayerView(
                    u,
                    await _views(uow, slots, worn.get(u, {})),
                    avatars[u].public_key if u in avatars else None,
                )
                for u in known
            ]
