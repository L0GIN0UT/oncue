"""Ручки магазина, инвентаря, оформления и промокода (5b, поток S1; ADR 0045, 0048).

* все ручки - только пользователь с аккаунтом (гость - 403 ``auth.guest_forbidden``);
* успешные ответы с данными игрока - ``Cache-Control: private, no-store``;
* мутации идемпотентны: покупка - по ``op_id = shop:{user}:{item}``, промокод - по паре
  ``(код, игрок)``, оформление - естественно (PUT слота); ``Idempotency-Key`` обязателен;
* паки скрыты (404 ``progression.billing_disabled``), пока ``billing.enabled=false``.

Заявка на свою аватарку - ``avatar.py`` (поток S3).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from gv_auth import Principal, require_user
from gv_service import IdempotencyKey, problem_responses
from progression_service.api.public.shop_schemas import (
    Appearance,
    AppearanceSlot,
    EquipRequest,
    InventoryEntry,
    ItemView,
    Key,
    MyInventory,
    PackCatalog,
    PackView,
    PlayerAppearance,
    PlayersAppearance,
    PromoRedeemRequest,
    PromoRedeemResult,
    PurchaseResult,
    ShopCatalog,
    ShopItem,
)
from progression_service.application.appearance import SlotView
from progression_service.application.shop import ShopServices
from progression_service.domain.shop import Item

User = Annotated[Principal, Depends(require_user)]
ItemIdPath = Annotated[UUID, Path(description="Идентификатор предмета")]
SlotPath = Annotated[
    str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Ключ слота")
]
PRIVATE = "private, no-store"
_READ = problem_responses(401, 403)


def register_shop_routes(router: APIRouter, services: ShopServices) -> None:
    """Добавить в ``/v1`` ручки магазина, инвентаря, оформления и промокода."""
    url = services.public_url

    def view(item: Item) -> ItemView:
        return ItemView(
            id=item.id,
            key=item.key,
            slot=item.slot,
            rarity=item.rarity,
            title=item.title,
            media_kind=item.media_kind.value,
            asset_url=url(item.asset_key) if item.asset_key else "",
            poster_url=url(item.poster_key) if item.poster_key else None,
            level_required=item.level_required,
            price=item.price,
            event_key=None,
        )

    def appearance(slots: list[SlotView]) -> Appearance:
        return Appearance(
            slots=[
                AppearanceSlot(slot=s.slot, item=None if s.item is None else view(s.item))
                for s in slots
            ]
        )

    @router.get(
        "/shop/items",
        response_model=ShopCatalog,
        summary="Витрина",
        description=(
            "Активные предметы с состоянием для игрока (`locked | available | owned`); "
            "фильтр по слоту. Условия открытия - по уровню и/или за Версы."
        ),
        responses=_READ,
    )
    async def shop_items(
        user: User,
        response: Response,
        slot: Annotated[Key | None, Query(description="Ключ слота")] = None,
    ) -> ShopCatalog:
        catalog = await services.shop.catalog(user.user_id, slot)
        response.headers["Cache-Control"] = PRIVATE
        return ShopCatalog(
            items=[ShopItem(item=view(e.item), state=e.state.value) for e in catalog.entries],
            balance=catalog.balance,
        )

    @router.post(
        "/shop/items/{item_id}/purchase",
        response_model=PurchaseResult,
        summary="Купить предмет",
        description=(
            "Одна транзакция: проводка `CurrencyDebited` (`op_id = shop:{user}:{item}`), "
            "строка инвентаря, outbox `ItemPurchased`. Пустое тело; цена и условия - только "
            "на сервере. Повтор - та же выдача (`repeated = true`). Ошибки: 409 "
            "`progression.insufficient_funds`, 409 `progression.already_owned`, 403 "
            "`progression.item_not_available`, 404 `progression.item_not_found`."
        ),
        responses=problem_responses(401, 403, 404, 409),
    )
    async def purchase(
        item_id: ItemIdPath, user: User, key: IdempotencyKey, response: Response
    ) -> PurchaseResult:
        outcome = await services.shop.purchase(user.user_id, item_id)
        response.headers["Cache-Control"] = PRIVATE
        return PurchaseResult(
            item=view(outcome.item), balance_after=outcome.balance_after, repeated=outcome.repeated
        )

    @router.get(
        "/shop/packs",
        response_model=PackCatalog,
        summary="Паки косметики",
        description="Скрыты, пока `billing.enabled=false`: 404 `progression.billing_disabled`.",
        responses=problem_responses(401, 403, 404),
    )
    async def shop_packs(user: User, response: Response) -> PackCatalog:
        found = await services.shop.packs()
        response.headers["Cache-Control"] = PRIVATE
        return PackCatalog(
            packs=[
                PackView(
                    id=p.id,
                    key=p.key,
                    title=p.title,
                    style=p.style,
                    items=[view(i) for i in items],
                    verses_amount=p.verses_amount,
                    price_kopecks=p.price_kopecks,
                )
                for p, items in found
            ]
        )

    @router.get(
        "/me/inventory",
        response_model=MyInventory,
        summary="Мой инвентарь",
        description="Предметы игрока; просроченные временные предметы скрыты.",
        responses=_READ,
    )
    async def my_inventory(user: User, response: Response) -> MyInventory:
        entries = await services.inventory.mine(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return MyInventory(
            items=[
                InventoryEntry(
                    item=view(e.item),
                    source=e.entry.source.value,
                    acquired_at=e.entry.acquired_at,
                    expires_at=e.entry.expires_at,
                    equipped=e.equipped,
                )
                for e in entries
            ]
        )

    @router.get(
        "/appearance",
        response_model=PlayersAppearance,
        summary="Оформление игроков",
        description=(
            "Публичное оформление (рамка, титул, фон, аватар) до 12 игроков за вызов: ник "
            "и аватар в матче и в профиле. Только пользователь с аккаунтом; неизвестные id "
            "пропускаются, дубли схлопываются; больше 12 - 422."
        ),
        responses=problem_responses(401, 403, 422),
    )
    async def players_appearance(
        user: User,
        response: Response,
        user_ids: Annotated[list[UUID], Query(min_length=1, max_length=12)],
    ) -> PlayersAppearance:
        players = await services.appearance.players(user_ids)
        response.headers["Cache-Control"] = PRIVATE
        return PlayersAppearance(
            players=[
                PlayerAppearance(
                    user_id=p.user_id,
                    appearance=appearance(p.slots),
                    custom_avatar_url=(
                        services.ugc_url(p.custom_avatar_key) if p.custom_avatar_key else None
                    ),
                )
                for p in players
            ]
        )

    @router.get(
        "/me/appearance",
        response_model=Appearance,
        summary="Моё оформление",
        description="По одному предмету на слот; пустой слот - `item = null`.",
        responses=_READ,
    )
    async def my_appearance(user: User, response: Response) -> Appearance:
        slots = await services.appearance.mine(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return appearance(slots)

    @router.put(
        "/me/appearance/{slot}",
        response_model=Appearance,
        summary="Надеть или снять предмет",
        description=(
            "`item_id = null` снимает. Предмет должен быть в инвентаре и подходить слоту. "
            "Идемпотентно по природе операции. Ошибки: 404 `progression.slot_not_found`, "
            "404 `progression.item_not_owned`, 422 `progression.item_slot_mismatch`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def equip(
        slot: SlotPath, body: EquipRequest, user: User, response: Response
    ) -> Appearance:
        slots = await services.appearance.equip(user.user_id, slot, body.item_id)
        response.headers["Cache-Control"] = PRIVATE
        return appearance(slots)

    @router.post(
        "/promo/redeem",
        response_model=PromoRedeemResult,
        summary="Погасить промокод",
        description=(
            "Награда - предмет или Версы. Повтор того же кода тем же игроком - та же награда "
            "(`repeated = true`). Неверный, просроченный и исчерпанный код дают одинаковый "
            "404 `progression.promo_invalid`; лимит попыток по игроку и IP - 429 "
            "`progression.promo_rate_limited` с `Retry-After`. Гостю - 403."
        ),
        responses=problem_responses(401, 403, 404, 422, 429, 503),
    )
    async def redeem(
        body: PromoRedeemRequest,
        user: User,
        key: IdempotencyKey,
        request: Request,
        response: Response,
    ) -> PromoRedeemResult:
        ip = request.client.host if request.client else None
        outcome = await services.promo.redeem(user.user_id, body.code, client_ip=ip)
        response.headers["Cache-Control"] = PRIVATE
        return PromoRedeemResult(
            reward_kind=outcome.reward_kind.value,
            amount=outcome.amount,
            item=None if outcome.item is None else view(outcome.item),
            repeated=outcome.repeated,
        )
