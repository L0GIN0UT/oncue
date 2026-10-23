"""Витрина и покупка предмета (ADR 0045): зона повышенной безопасности (траты Версов).

Покупка - одна транзакция: блокировка ``progress`` игрока (сериализует все его операции) ->
проверка повтора по ``op_id = shop:{user}:{item}`` -> владение -> цена и баланс ``earned`` под
``FOR UPDATE`` -> проводка ``CurrencyDebited`` -> строка инвентаря -> outbox ``ItemPurchased``.
Любой сбой откатывает всё. Цена и условия берутся только из БД, клиент передаёт лишь id.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster
from progression_service.application.appearance import AppearanceService
from progression_service.application.avatar import AvatarService
from progression_service.application.inventory import (
    InventoryService,
    shop_config,
    unlock_by_level,
    user_level,
)
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.application.promo import PromoService
from progression_service.domain import shop
from progression_service.domain.events import ItemPurchased
from progression_service.domain.inventory import InventoryItem, Source
from progression_service.domain.ledger import Bucket, debit
from progression_service.domain.packs import BillingDisabledError, Pack
from progression_service.domain.shop import Item, ItemState

log = structlog.get_logger(__name__)

PURCHASE_REASON = "shop"


@dataclass(frozen=True, slots=True)
class ShopEntry:
    """Предмет витрины с состоянием для игрока."""

    item: Item
    state: ItemState


@dataclass(frozen=True, slots=True)
class Catalog:
    """Витрина: предметы и баланс ``earned``."""

    entries: list[ShopEntry]
    balance: int


@dataclass(frozen=True, slots=True)
class PurchaseOutcome:
    """Результат покупки; ``repeated`` - проводка уже была."""

    item: Item
    balance_after: int
    repeated: bool


@dataclass(frozen=True, slots=True)
class ShopServices:
    """Use-cases 5b для публичного API (собираются в composition root)."""

    shop: ShopService
    inventory: InventoryService
    appearance: AppearanceService
    promo: PromoService
    public_url: Callable[[str], str]
    avatars: AvatarService
    ugc_url: Callable[[str], str]


class ShopService:
    """Витрина, покупка, витрина паков."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, *, billing_enabled: bool) -> None:
        self._uow = uow
        self._clock = clock
        self._billing_enabled = billing_enabled

    async def catalog(self, user_id: UUID, slot: str | None) -> Catalog:
        """Активные предметы с состоянием; сначала открывается всё, что дал уровень."""
        async with self._uow() as uow:
            now = self._clock.now()
            level = await user_level(uow, user_id)
            await unlock_by_level(uow, user_id, now, level=level)
            items = await uow.items.list_active(slot) if await shop_config(uow) else []
            owned = await uow.inventory.owned_ids(user_id, now)
            balance = (await uow.balances.all(user_id)).get(Bucket.EARNED, 0)
            await uow.commit()
        entries = [
            ShopEntry(i, shop.item_state(i, level=level, owned=i.id in owned)) for i in items
        ]
        return Catalog(entries, balance)

    async def purchase(self, user_id: UUID, item_id: UUID) -> PurchaseOutcome:
        """Купить предмет за Версы бакета ``earned``.

        Повтор той же пары (игрок, предмет) возвращает прежнюю выдачу без новой проводки.

        Raises:
            ItemNotFoundError: предмета нет или он неактивен (404).
            ItemNotAvailableError: предмет открывается только уровнем (403).
            AlreadyOwnedError: предмет уже есть (409).
            InsufficientFundsError: не хватает Версов (409).
        """
        async with self._uow() as uow:
            published = await published_config(uow)
            now = self._clock.now()
            poster = Poster(uow, published, now)
            progress = await poster.lock_user(user_id)
            item = await uow.items.get(item_id)
            if item is None:
                raise shop.ItemNotFoundError
            op_id = shop.purchase_op_id(user_id, item_id)
            if await uow.ledger.has_currency(op_id):
                balance = (await uow.balances.all(user_id)).get(Bucket.EARNED, 0)
                return PurchaseOutcome(item, balance, repeated=True)
            await unlock_by_level(uow, user_id, now, level=progress.level)
            owned = await uow.inventory.get(user_id, item_id, now) is not None
            price = shop.check_purchasable(item, owned=owned)
            balance = await uow.balances.lock(user_id, Bucket.EARNED, now=now)
            shop.check_funds(price, balance)
            await poster.post_currency(
                debit(
                    op_id=op_id,
                    user_id=user_id,
                    amount=price,
                    reason=PURCHASE_REASON,
                    source_ref=str(item_id),
                    config_version=published.version,
                    at=now,
                    bucket=Bucket.EARNED,
                )
            )
            await uow.inventory.add(
                InventoryItem(user_id, item_id, Source.PURCHASE, now, None, None)
            )
            uow.publish(
                ItemPurchased(
                    aggregate_id=str(user_id),
                    user_id=user_id,
                    item_id=item.id,
                    item_key=item.key,
                    slot=item.slot,
                    rarity=item.rarity,
                    price=price,
                    balance_after=balance - price,
                    op_id=op_id,
                    at=now,
                )
            )
            await uow.commit()
        log.info("progression.item_purchased", item_key=item.key, price=price)
        return PurchaseOutcome(item, balance - price, repeated=False)

    async def packs(self) -> list[tuple[Pack, list[Item]]]:
        """Витрина паков.

        Raises:
            BillingDisabledError: ``billing.enabled=false`` - витрина скрыта (404).
        """
        if not self._billing_enabled:
            raise BillingDisabledError
        async with self._uow() as uow:
            found = await uow.packs.listing(active_only=True)
            items = await uow.items.by_ids({i for p in found for i in p.item_ids})
        return [(p, [items[i] for i in p.item_ids if i in items]) for p in found]
