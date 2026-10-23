"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.5, ADR 0047).

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
from notifications_service.api.schemas import Broadcast, BroadcastPage, BroadcastRequest, Cursor
from notifications_service.api.services import ApiServices
from notifications_service.application.broadcasts import broadcast_json

Sender = Annotated[Principal, Depends(require_perm("admin.access", "notifications.broadcast.send"))]
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

    return router
