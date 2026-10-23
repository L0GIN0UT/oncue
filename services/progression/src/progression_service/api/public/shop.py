"""Ручки магазина, инвентаря, оформления, промокода и своей аватарки (5b).

Контракт этапа 5b (``docs/plans/stage-5.md`` §9, ADR 0045, 0048). Шаг 0a: ручки объявлены,
реализация - поток S1 (магазин, инвентарь, оформление, промокоды, паки) и S3 (аватарка);
до тех пор отвечают 501 ``common.not_implemented``.

* все ручки - только пользователь с аккаунтом (гость - 403 ``auth.guest_forbidden``);
* ответы с данными игрока - ``private, no-store``;
* мутации идемпотентны: покупка и промокод - по ``Idempotency-Key`` и предмету операции,
  оформление - естественно (PUT слота), заявка на аватарку - по ``Idempotency-Key``;
* паки скрыты (404 ``progression.billing_disabled``), пока ``billing.enabled=false``.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile

from gv_auth import Principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from progression_service.api.public.shop_schemas import (
    Appearance,
    AvatarRequestView,
    EquipRequest,
    Key,
    MyAvatar,
    MyInventory,
    PackCatalog,
    PlayersAppearance,
    PromoRedeemRequest,
    PromoRedeemResult,
    PurchaseResult,
    ShopCatalog,
)

User = Annotated[Principal, Depends(require_user)]
ItemIdPath = Annotated[UUID, Path(description="Идентификатор предмета")]
SlotPath = Annotated[
    str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Ключ слота")
]
_READ = problem_responses(401, 403, 501)
_WRITE = problem_responses(401, 403, 409, 422, 501)


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоках S1/S3 этапа 5b")


def register_shop_routes(router: APIRouter) -> None:
    """Добавить в ``/v1`` ручки 5b (пока заглушки)."""

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
        slot: Annotated[Key | None, Query(description="Ключ слота")] = None,
    ) -> ShopCatalog:
        raise _stub()

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
        responses=problem_responses(401, 403, 404, 409, 501),
    )
    async def purchase(item_id: ItemIdPath, user: User, key: IdempotencyKey) -> PurchaseResult:
        raise _stub()

    @router.get(
        "/shop/packs",
        response_model=PackCatalog,
        summary="Паки косметики",
        description="Скрыты, пока `billing.enabled=false`: 404 `progression.billing_disabled`.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def shop_packs(user: User) -> PackCatalog:
        raise _stub()

    @router.get(
        "/me/inventory",
        response_model=MyInventory,
        summary="Мой инвентарь",
        description="Предметы игрока; просроченные временные предметы скрыты.",
        responses=_READ,
    )
    async def my_inventory(user: User) -> MyInventory:
        raise _stub()

    @router.get(
        "/appearance",
        response_model=PlayersAppearance,
        summary="Оформление игроков",
        description=(
            "Публичное оформление (рамка, титул, фон, аватар) до 12 игроков за вызов: ник "
            "и аватар в матче и в профиле. Только пользователь с аккаунтом; неизвестные id "
            "пропускаются, дубли схлопываются; больше 12 - 422."
        ),
        responses=problem_responses(401, 403, 422, 501),
    )
    async def players_appearance(
        user: User,
        user_ids: Annotated[list[UUID], Query(min_length=1, max_length=12)],
    ) -> PlayersAppearance:
        raise _stub()

    @router.get(
        "/me/appearance",
        response_model=Appearance,
        summary="Моё оформление",
        description="По одному предмету на слот; пустой слот - `item = null`.",
        responses=_READ,
    )
    async def my_appearance(user: User) -> Appearance:
        raise _stub()

    @router.put(
        "/me/appearance/{slot}",
        response_model=Appearance,
        summary="Надеть или снять предмет",
        description=(
            "`item_id = null` снимает. Предмет должен быть в инвентаре и подходить слоту. "
            "Идемпотентно по природе операции. Ошибки: 404 `progression.slot_not_found`, "
            "404 `progression.item_not_owned`, 422 `progression.item_slot_mismatch`."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def equip(slot: SlotPath, body: EquipRequest, user: User) -> Appearance:
        raise _stub()

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
        responses=problem_responses(401, 403, 404, 422, 429, 501),
    )
    async def redeem(
        body: PromoRedeemRequest, user: User, key: IdempotencyKey
    ) -> PromoRedeemResult:
        raise _stub()

    @router.get(
        "/me/avatar",
        response_model=MyAvatar,
        summary="Моя аватарка",
        description="Куплено ли право, действующий аватар, последняя заявка и лимиты загрузки.",
        responses=_READ,
    )
    async def my_avatar(user: User) -> MyAvatar:
        raise _stub()

    @router.post(
        "/me/avatar",
        response_model=AvatarRequestView,
        status_code=202,
        summary="Загрузить свою аватарку",
        description=(
            "multipart, поле `file`. Нужен купленный товар "
            "(403 `progression.avatar_not_entitled`). "
            "Файл уходит в media-pipeline (проверка, пересжатие, UGC-ассет `pending`); до решения "
            "модератора виден прежний аватар. Новая заявка отменяет предыдущую `pending`. "
            "Ошибки файла: 422 `ugc.invalid_image`, 413 `ugc.too_large`."
        ),
        responses=problem_responses(401, 403, 413, 422, 501),
    )
    async def submit_avatar(
        user: User,
        key: IdempotencyKey,
        file: Annotated[UploadFile, File(description="Изображение")],
    ) -> AvatarRequestView:
        raise _stub()
