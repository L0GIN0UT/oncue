"""Публичный API (/v1). На gateway: /api/notifications/v1/....

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.5, ADR 0047). Шаг 0a: ручки объявлены,
реализация - поток S2; до тех пор отвечают 501 ``common.not_implemented``.

* все ручки, кроме отписки по ссылке, - только пользователь с аккаунтом (гость - 403);
* уведомления читает только владелец (чужое - 404); отметка прочитанным идемпотентна;
* ответы с данными игрока - ``private, no-store``.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001, FBT002
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from gv_auth import Principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Infrastructure, problem_responses
from notifications_service.api.schemas import (
    Cursor,
    Device,
    DeviceRequest,
    EmailSubscription,
    EmailSubscriptionRequest,
    MarkedRead,
    NotificationPage,
    NotificationSettings,
    TypeSetting,
    TypeSettingRequest,
    UnreadCount,
    UnsubscribeRequest,
)

User = Annotated[Principal, Depends(require_user)]
IdPath = Annotated[UUID, Path(description="Идентификатор")]
TypePath = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Тип")]
_READ = problem_responses(401, 403, 501)


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоке S2 этапа 5b")


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["notifications"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    @router.get(
        "/notifications",
        response_model=NotificationPage,
        summary="Мои уведомления",
        description="Новые первыми, курсорная пагинация; `unread_only` - только непрочитанные.",
        responses=_READ,
    )
    async def notifications(
        user: User,
        unread_only: Annotated[bool, Query()] = False,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> NotificationPage:
        raise _stub()

    @router.get(
        "/notifications/unread-count",
        response_model=UnreadCount,
        summary="Число непрочитанных",
        responses=_READ,
    )
    async def unread_count(user: User) -> UnreadCount:
        raise _stub()

    @router.post(
        "/notifications/read-all",
        response_model=MarkedRead,
        summary="Прочитать все",
        description="Идемпотентно: повтор отмечает 0.",
        responses=_READ,
    )
    async def read_all(user: User) -> MarkedRead:
        raise _stub()

    @router.post(
        "/notifications/{notification_id}/read",
        status_code=204,
        summary="Отметить прочитанным",
        description="Идемпотентно. Чужое или несуществующее - 404 `notifications.not_found`.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def read_one(notification_id: IdPath, user: User) -> None:
        raise _stub()

    @router.get(
        "/notification-settings",
        response_model=NotificationSettings,
        summary="Настройки уведомлений",
        description="Типы из конфига с текущими каналами игрока.",
        responses=_READ,
    )
    async def settings(user: User) -> NotificationSettings:
        raise _stub()

    @router.put(
        "/notification-settings/{type_key}",
        response_model=TypeSetting,
        summary="Изменить настройку типа",
        description=(
            "Идемпотентно по природе операции. Неизвестный тип - 404 "
            "`notifications.type_not_found`; тип с `user_mutable = false` - 422 "
            "`notifications.type_not_mutable`."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def set_setting(type_key: TypePath, body: TypeSettingRequest, user: User) -> TypeSetting:
        raise _stub()

    @router.get(
        "/email-subscription",
        response_model=EmailSubscription,
        summary="Согласие на рассылку",
        responses=_READ,
    )
    async def email_subscription(user: User) -> EmailSubscription:
        raise _stub()

    @router.put(
        "/email-subscription",
        response_model=EmailSubscription,
        summary="Дать или отозвать согласие на рассылку",
        description="По умолчанию согласия нет; рассылка без согласия не уходит.",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def set_email_subscription(
        body: EmailSubscriptionRequest, user: User
    ) -> EmailSubscription:
        raise _stub()

    @router.post(
        "/unsubscribe",
        status_code=204,
        summary="Отписаться по ссылке из письма",
        description=(
            "Единственная ручка без входа. Токен - HMAC серверным ключом над `user_id` и "
            "рассылкой, сравнение за постоянное время; чужой, просроченный и неверный токен "
            "дают один и тот же ответ (существование пользователя не раскрывается). Лимит "
            "попыток по IP (Redis, fail-closed): 429 с `Retry-After`. Повтор - 204. "
            "Неверный токен - 422 `notifications.unsubscribe_invalid`."
        ),
        responses=problem_responses(422, 429, 501),
    )
    async def unsubscribe(body: UnsubscribeRequest) -> None:
        raise _stub()

    @router.post(
        "/devices",
        response_model=Device,
        status_code=201,
        summary="Зарегистрировать устройство для Push",
        description=(
            "Push пока не отправляется (порт без реализации); повтор токена - то же устройство."
        ),
        responses=problem_responses(401, 403, 422, 501),
    )
    async def register_device(body: DeviceRequest, user: User, key: IdempotencyKey) -> Device:
        raise _stub()

    @router.delete(
        "/devices/{device_id}",
        status_code=204,
        summary="Удалить устройство",
        description="Идемпотентно; чужое устройство - 404.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def delete_device(device_id: IdPath, user: User) -> None:
        raise _stub()

    return router
