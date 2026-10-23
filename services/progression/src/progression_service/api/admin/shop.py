"""Admin API 5b: предметы, паки, промокоды.

Контракт этапа 5b (``docs/plans/stage-5.md`` §9, ADR 0045, 0046, 0048). Шаг 0a: ручки
объявлены, реализация - поток S1 (данные) и S4 (раздел админки); до тех пор 501.

Права: ``admin.access`` + ``progression.items.manage`` (предметы, паки, загрузка медиа) или
``progression.promo.manage`` (промокоды). Мутации - ``Idempotency-Key`` и ``reason``
(в аудит); ответы без кэша. Модерация аватарок - в media-pipeline (``media.ugc.moderate``).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Reason, problem_responses
from progression_service.api.admin.shop_schemas import (
    AdminItem,
    AdminItemPage,
    AdminPack,
    AdminPackList,
    CreatedPromo,
    CreateItemRequest,
    CreatePromoRequest,
    Cursor,
    PromoBatchPage,
    RevokeRequest,
    SavePackRequest,
    UpdateItemRequest,
)
from progression_service.api.public.shop_schemas import Key

ADMIN_ACCESS = "admin.access"
ItemsManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.items.manage"))]
PromoManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.promo.manage"))]
IdPath = Annotated[UUID, Path(description="Идентификатор")]
_MUTATE = problem_responses(401, 403, 404, 409, 422, 501)


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоках S1/S4 этапа 5b")


def register_shop_admin_routes(router: APIRouter) -> None:
    """Добавить в ``/admin/v1`` ручки 5b (пока заглушки)."""

    @router.get(
        "/items",
        response_model=AdminItemPage,
        summary="Предметы",
        responses=problem_responses(401, 403, 501),
    )
    async def items(
        principal: ItemsManager,
        slot: Annotated[Key | None, Query()] = None,
        active: Annotated[bool | None, Query()] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AdminItemPage:
        raise _stub()

    @router.post(
        "/items",
        response_model=AdminItem,
        status_code=201,
        summary="Создать предмет",
        description=(
            "Без картинки предмет неактивен. Слот и редкость - из секции `shop` действующего "
            "конфига (иначе 422 `progression.item_slot_unknown` или "
            "`progression.item_rarity_unknown`); "
            "нужно `level_required` или `price` (422 `progression.item_unlock_required`); "
            "повтор `key` - 409 `progression.item_key_taken`."
        ),
        responses=_MUTATE,
    )
    async def create_item(
        body: CreateItemRequest, principal: ItemsManager, key: IdempotencyKey
    ) -> AdminItem:
        raise _stub()

    @router.put(
        "/items/{item_id}",
        response_model=AdminItem,
        summary="Изменить предмет",
        description=(
            "Цена и условия действуют для новых покупок; владельцы не затрагиваются. "
            "Активировать без картинки нельзя - 422 `progression.item_media_missing`."
        ),
        responses=_MUTATE,
    )
    async def update_item(
        item_id: IdPath, body: UpdateItemRequest, principal: ItemsManager, key: IdempotencyKey
    ) -> AdminItem:
        raise _stub()

    @router.put(
        "/items/{item_id}/media",
        response_model=AdminItem,
        summary="Загрузить картинку или анимацию предмета",
        description=(
            "multipart: `file`, `reason`. Статика - WebP/PNG, анимация - animated WebP "
            "(ADR 0046); проверка сигнатуры, декодирование, лимиты размера, кадров, "
            "длительности и стороны из секции `shop.media` конфига, постер - первый кадр. "
            "Ошибки: 422 `progression.item_media_invalid`, 413 `progression.item_media_too_large`."
        ),
        responses=problem_responses(401, 403, 404, 413, 422, 501),
    )
    async def upload_item_media(
        item_id: IdPath,
        principal: ItemsManager,
        key: IdempotencyKey,
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Картинка или анимация")],
    ) -> AdminItem:
        raise _stub()

    @router.get(
        "/packs",
        response_model=AdminPackList,
        summary="Паки",
        responses=problem_responses(401, 403, 501),
    )
    async def packs(principal: ItemsManager) -> AdminPackList:
        raise _stub()

    @router.post(
        "/packs",
        response_model=AdminPack,
        status_code=201,
        summary="Создать пак",
        description="Только модель: витрина и покупка скрыты, пока `billing.enabled=false`.",
        responses=_MUTATE,
    )
    async def create_pack(
        body: SavePackRequest, principal: ItemsManager, key: IdempotencyKey
    ) -> AdminPack:
        raise _stub()

    @router.put(
        "/packs/{pack_id}",
        response_model=AdminPack,
        summary="Заменить пак",
        responses=_MUTATE,
    )
    async def update_pack(
        pack_id: IdPath, body: SavePackRequest, principal: ItemsManager, key: IdempotencyKey
    ) -> AdminPack:
        raise _stub()

    @router.get(
        "/promo-codes",
        response_model=PromoBatchPage,
        summary="Партии промокодов",
        description="Без самих кодов: в БД только хеш.",
        responses=problem_responses(401, 403, 501),
    )
    async def promo_batches(
        principal: PromoManager,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> PromoBatchPage:
        raise _stub()

    @router.post(
        "/promo-codes",
        response_model=CreatedPromo,
        status_code=201,
        summary="Сгенерировать партию промокодов",
        description=(
            "Коды генерируются сервером (длина - `promo.code_length` конфига) и возвращаются "
            "ОДИН раз. Награда - предмет или Версы; для предмета он должен быть активен "
            "(422 `progression.item_not_found`). Повтор с тем же `Idempotency-Key` не создаёт "
            "вторую партию и не раскрывает коды повторно (409 `progression.codes_already_shown`)."
        ),
        responses=_MUTATE,
    )
    async def create_promo(
        body: CreatePromoRequest, principal: PromoManager, key: IdempotencyKey
    ) -> CreatedPromo:
        raise _stub()

    @router.post(
        "/promo-codes/{promo_id}/revoke",
        status_code=204,
        summary="Отозвать партию",
        description="Новые активации невозможны; выданные награды не отзываются.",
        responses=_MUTATE,
    )
    async def revoke_promo(
        promo_id: IdPath, body: RevokeRequest, principal: PromoManager, key: IdempotencyKey
    ) -> None:
        raise _stub()
