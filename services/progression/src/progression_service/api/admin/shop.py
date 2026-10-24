"""Admin API 5b: предметы, паки, промокоды (поток S1; ADR 0045, 0046, 0048).

Права: ``admin.access`` + ``progression.items.manage`` (предметы, паки, загрузка медиа) или
``progression.promo.manage`` (промокоды). Мутации - ``Idempotency-Key`` и ``reason``
(в аудит); успешные ответы - ``Cache-Control: no-store`` (ставится после выполнения, на
Problem заголовок не переносится).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001, PLR0917
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, Reason, problem_responses
from gv_service.problems import problem
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
from progression_service.api.admin.shop_schemas import (
    ItemFields as ItemBody,
)
from progression_service.api.admin.shop_schemas import (
    PackFields as PackBody,
)
from progression_service.api.public.shop_schemas import Key
from progression_service.application.admin import AdminServices
from progression_service.application.admin.common import AdminContext
from progression_service.application.admin.shop import ItemFields, PackFields, PromoFields
from progression_service.domain.config import BOUNDS
from progression_service.domain.shop import ItemMediaTooLargeError

ADMIN_ACCESS = "admin.access"
ItemsManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.items.manage"))]
PromoManager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.promo.manage"))]
IdPath = Annotated[UUID, Path(description="Идентификатор")]
NO_STORE = "no-store"
_MUTATE = problem_responses(401, 403, 404, 409, 422)
_UPLOAD_CEILING = int(BOUNDS["shop.media.animated_max_bytes"])
"""Наибольший файл, который ручка вообще читает в память: граница схемы конфига. Точный лимит
вида предмета берётся из действующего конфига при разборе."""


def _too_large(error: ItemMediaTooLargeError) -> JSONResponse:
    """413 Problem: у ``AppError`` нет статуса 413, поэтому собираем ответ сами (и без кэша)."""
    result = problem(413, error.code, error.message)
    result.headers["Cache-Control"] = NO_STORE
    return result


def _ctx(principal: Principal, reason: str, key: str) -> AdminContext:
    return AdminContext(actor_id=principal.user_id, reason=reason, idempotency_key=key)


def _item_fields(body: ItemBody) -> ItemFields:
    return ItemFields(
        key=body.key,
        slot=body.slot,
        rarity=body.rarity,
        title=body.title,
        media_kind=body.media_kind,
        level_required=body.level_required,
        price=body.price,
        active=body.active,
        event_key=body.event_key,
    )


def _pack_fields(body: PackBody) -> PackFields:
    return PackFields(
        key=body.key,
        title=body.title,
        style=body.style,
        item_ids=tuple(body.item_ids),
        verses_amount=body.verses_amount,
        price_kopecks=body.price_kopecks,
        active=body.active,
    )


def register_shop_admin_routes(router: APIRouter, services: AdminServices) -> None:
    """Добавить в ``/admin/v1`` ручки предметов, паков и промокодов."""

    @router.get(
        "/items",
        response_model=AdminItemPage,
        summary="Предметы",
        responses=problem_responses(401, 403),
    )
    async def items(
        principal: ItemsManager,
        response: Response,
        slot: Annotated[Key | None, Query()] = None,
        active: Annotated[bool | None, Query()] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AdminItemPage:
        page = await services.item_queries.page(
            slot=slot, active=active, cursor=cursor, limit=limit
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminItemPage.model_validate(page)

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
        body: CreateItemRequest, principal: ItemsManager, key: IdempotencyKey, response: Response
    ) -> AdminItem:
        result = await services.items.create(
            _item_fields(body), ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminItem.model_validate(result)

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
        item_id: IdPath,
        body: UpdateItemRequest,
        principal: ItemsManager,
        key: IdempotencyKey,
        response: Response,
    ) -> AdminItem:
        result = await services.items.update(
            item_id, _item_fields(body), ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminItem.model_validate(result)

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
        responses=problem_responses(401, 403, 404, 413, 422),
    )
    async def upload_item_media(
        item_id: IdPath,
        principal: ItemsManager,
        key: IdempotencyKey,
        response: Response,
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Картинка или анимация")],
    ) -> AdminItem | JSONResponse:
        data = await file.read(_UPLOAD_CEILING + 1)
        if len(data) > _UPLOAD_CEILING:
            return _too_large(ItemMediaTooLargeError())
        try:
            result = await services.items.upload_media(
                item_id, data, ctx=_ctx(principal, reason, key)
            )
        except ItemMediaTooLargeError as exc:
            return _too_large(exc)
        response.headers["Cache-Control"] = NO_STORE
        return AdminItem.model_validate(result)

    @router.get(
        "/packs",
        response_model=AdminPackList,
        summary="Паки",
        responses=problem_responses(401, 403),
    )
    async def packs(principal: ItemsManager, response: Response) -> AdminPackList:
        result = await services.pack_queries.all()
        response.headers["Cache-Control"] = NO_STORE
        return AdminPackList.model_validate(result)

    @router.post(
        "/packs",
        response_model=AdminPack,
        status_code=201,
        summary="Создать пак",
        description="Только модель: витрина и покупка скрыты, пока `billing.enabled=false`.",
        responses=_MUTATE,
    )
    async def create_pack(
        body: SavePackRequest, principal: ItemsManager, key: IdempotencyKey, response: Response
    ) -> AdminPack:
        result = await services.packs.create(
            _pack_fields(body), ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminPack.model_validate(result)

    @router.put(
        "/packs/{pack_id}",
        response_model=AdminPack,
        summary="Заменить пак",
        responses=_MUTATE,
    )
    async def update_pack(
        pack_id: IdPath,
        body: SavePackRequest,
        principal: ItemsManager,
        key: IdempotencyKey,
        response: Response,
    ) -> AdminPack:
        result = await services.packs.replace(
            pack_id, _pack_fields(body), ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminPack.model_validate(result)

    @router.get(
        "/promo-codes",
        response_model=PromoBatchPage,
        summary="Партии промокодов",
        description="Без самих кодов: в БД только хеш.",
        responses=problem_responses(401, 403),
    )
    async def promo_batches(
        principal: PromoManager,
        response: Response,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> PromoBatchPage:
        result = await services.promo_queries.page(cursor=cursor, limit=limit)
        response.headers["Cache-Control"] = NO_STORE
        return PromoBatchPage.model_validate(result)

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
        body: CreatePromoRequest, principal: PromoManager, key: IdempotencyKey, response: Response
    ) -> CreatedPromo:
        result = await services.promos.create(
            PromoFields(
                label=body.label,
                reward_kind=body.reward.kind,
                verses_amount=body.reward.verses_amount,
                item_id=body.reward.item_id,
                count=body.count,
                max_uses=body.max_uses,
                per_user_once=body.per_user_once,
                expires_at=body.expires_at,
            ),
            ctx=_ctx(principal, body.reason, key),
        )
        response.headers["Cache-Control"] = NO_STORE
        return CreatedPromo.model_validate(result)

    @router.post(
        "/promo-codes/{promo_id}/revoke",
        status_code=204,
        summary="Отозвать партию",
        description="Новые активации невозможны; выданные награды не отзываются.",
        responses=_MUTATE,
    )
    async def revoke_promo(
        promo_id: IdPath, body: RevokeRequest, principal: PromoManager, key: IdempotencyKey
    ) -> Response:
        await services.promos.revoke(promo_id, ctx=_ctx(principal, body.reason, key))
        return Response(status_code=204, headers={"Cache-Control": NO_STORE})
