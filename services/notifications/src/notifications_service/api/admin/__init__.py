"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.5, ADR 0047). Шаг 0a: ручки объявлены,
реализация - поток S2/S4; до тех пор отвечают 501 ``common.not_implemented``.

Рассылка: ``admin.access`` + ``notifications.broadcast.send``, ``Idempotency-Key`` и ``reason``
(в аудит). Получатели - только подписавшиеся (согласие), в письме ссылка отписки.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from notifications_service.api.schemas import Broadcast, BroadcastPage, BroadcastRequest, Cursor

Sender = Annotated[Principal, Depends(require_perm("admin.access", "notifications.broadcast.send"))]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоках S2/S4 этапа 5b")


def build_admin_router() -> APIRouter:
    """Ручки админки notifications."""
    router = APIRouter(prefix="/admin/v1", tags=["notifications-admin"])

    @router.get(
        "/broadcasts",
        response_model=BroadcastPage,
        summary="Рассылки",
        responses=problem_responses(401, 403, 501),
    )
    async def broadcasts(
        principal: Sender,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> BroadcastPage:
        raise _stub()

    @router.post(
        "/broadcasts",
        response_model=Broadcast,
        status_code=202,
        summary="Поставить рассылку",
        description=(
            "Информационное письмо подписчикам (согласие и отписка). Отправку делает задача "
            "RabbitMQ. Повтор с тем же `Idempotency-Key` возвращает ту же рассылку."
        ),
        responses=problem_responses(401, 403, 409, 422, 501),
    )
    async def create_broadcast(
        body: BroadcastRequest, principal: Sender, key: IdempotencyKey
    ) -> Broadcast:
        raise _stub()

    return router
