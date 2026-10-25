"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.5, ADR 0047).

Типы уведомлений: ``admin.access`` + ``notifications.types.manage`` - список и включение/
выключение (``Idempotency-Key``, ``reason`` в аудит; ADR 0066).

Рассылка: ``admin.access`` + ``notifications.broadcast.send``, ``Idempotency-Key`` и ``reason``
(в аудит). Получатели - только подписавшиеся (согласие), в письме ссылка отписки. Успешные
ответы - ``Cache-Control: no-store``.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, problem_responses
from notifications_service.api.schemas import (
    Broadcast,
    BroadcastAudience,
    BroadcastPage,
    BroadcastRequest,
    Cursor,
    Key,
    NotificationTypeAdmin,
    NotificationTypeAdminPage,
    NotificationTypeEnabledRequest,
)
from notifications_service.api.services import ApiServices
from notifications_service.application.broadcasts import broadcast_json

Sender = Annotated[Principal, Depends(require_perm("admin.access", "notifications.broadcast.send"))]
TypesManager = Annotated[
    Principal, Depends(require_perm("admin.access", "notifications.types.manage"))
]
NO_STORE = "no-store"


def build_admin_router(services: ApiServices) -> APIRouter:
    """Ручки админки notifications."""
    router = APIRouter(prefix="/admin/v1", tags=["notifications-admin"])

    @router.get(
        "/broadcasts",
        response_model=BroadcastPage,
        summary="Рассылки",
        responses=problem_responses(401, 403, 422),
    )
    async def broadcasts(
        principal: Sender,
        response: Response,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> BroadcastPage:
        page = await services.broadcast_queries.page(cursor=cursor, limit=limit)
        response.headers["Cache-Control"] = NO_STORE
        return BroadcastPage(
            items=[Broadcast.model_validate(broadcast_json(row)) for row in page.items],
            next_cursor=page.next_cursor,
        )

    @router.get(
        "/broadcasts/audience",
        response_model=BroadcastAudience,
        summary="Сколько получателей у рассылки",
        description=(
            "Для экрана подтверждения: число игроков с согласием, которым уйдёт рассылка "
            "(то же, что попадёт в `recipients` при создании). Запрос ограничен по времени: "
            "превышение - 503."
        ),
        responses=problem_responses(401, 403, 503),
    )
    async def broadcast_audience(principal: Sender, response: Response) -> BroadcastAudience:
        recipients = await services.broadcast_queries.audience()
        response.headers["Cache-Control"] = NO_STORE
        return BroadcastAudience(recipients=recipients)

    @router.post(
        "/broadcasts",
        response_model=Broadcast,
        status_code=202,
        summary="Поставить рассылку",
        description=(
            "Информационное письмо подписчикам (согласие и отписка). Отправку делает задача "
            "RabbitMQ. Повтор с тем же `Idempotency-Key` возвращает ту же рассылку."
        ),
        responses=problem_responses(401, 403, 409, 422),
    )
    async def create_broadcast(
        body: BroadcastRequest, principal: Sender, key: IdempotencyKey, response: Response
    ) -> Broadcast:
        saved, _ = await services.create_broadcast.execute(
            actor_id=principal.user_id,
            key=key,
            subject=body.subject,
            body=body.body,
            reason=body.reason,
        )
        response.headers["Cache-Control"] = NO_STORE
        return Broadcast.model_validate(saved)

    @router.get(
        "/notification-types",
        response_model=NotificationTypeAdminPage,
        summary="Типы уведомлений и их включённость",
        responses=problem_responses(401, 403),
    )
    async def notification_types(
        principal: TypesManager, response: Response
    ) -> NotificationTypeAdminPage:
        states = await services.type_admin.states()
        response.headers["Cache-Control"] = NO_STORE
        return NotificationTypeAdminPage(
            items=[
                NotificationTypeAdmin(
                    type=s.type_key,
                    enabled=s.enabled,
                    default_enabled=s.default_enabled,
                    changed_by=s.changed_by,
                    changed_at=s.changed_at,
                    switchable=s.switchable,
                )
                for s in states
            ]
        )

    @router.put(
        "/notification-types/{type_key}",
        response_model=NotificationTypeAdmin,
        summary="Включить или выключить тип уведомлений",
        description=(
            "Выключенный тип не создаёт уведомлений (события принимаются без ошибки, настройки "
            "игроков и рассылки не затрагиваются). Решение вступает в силу сразу в этом процессе, "
            "в остальных - не позже чем через `GV_NOTIFICATIONS__TYPE_FLAGS_CACHE_TTL_S`. "
            "Системные типы (`switchable = false`: итоги модерации) выключить нельзя - 422 "
            "`notifications.type_not_switchable`. "
            "Повтор с тем же `Idempotency-Key` возвращает тот же ответ."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def set_type_enabled(
        type_key: Key,
        body: NotificationTypeEnabledRequest,
        principal: TypesManager,
        key: IdempotencyKey,
        response: Response,
    ) -> NotificationTypeAdmin:
        saved = await services.type_admin.set_enabled(
            actor_id=principal.user_id,
            key=key,
            type_key=type_key,
            enabled=body.enabled,
            reason=body.reason,
        )
        response.headers["Cache-Control"] = NO_STORE
        return NotificationTypeAdmin.model_validate(saved)

    return router
