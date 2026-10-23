"""Публичный API (/v1). На gateway: /api/notifications/v1/....

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.5, ADR 0047):

* все ручки, кроме отписки по ссылке, - только пользователь с аккаунтом (гость - 403);
* уведомления читает только владелец (чужое - 404); отметка прочитанным идемпотентна;
* успешные ответы - ``Cache-Control: private, no-store`` (ставится после выполнения: ошибка
  уходит своим Problem-ответом без этого заголовка).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001, FBT002
from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel

from gv_auth import Principal, require_user
from gv_service import IdempotencyKey, Infrastructure, problem_responses
from notifications_service.api.schemas import (
    Cursor,
    Device,
    DeviceRequest,
    EmailSubscription,
    EmailSubscriptionRequest,
    MarkedRead,
    NotificationItem,
    NotificationPage,
    NotificationSettings,
    Platform,
    TypeSetting,
    TypeSettingRequest,
    UnreadCount,
    UnsubscribeRequest,
)
from notifications_service.api.services import ApiServices
from notifications_service.application.player import TypeState
from notifications_service.domain.notification import Notification

User = Annotated[Principal, Depends(require_user)]
IdPath = Annotated[UUID, Path(description="Идентификатор")]
TypePath = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Тип")]
_READ = problem_responses(401, 403, 422)
PRIVATE = "private, no-store"
UNKNOWN_CLIENT = "unknown"


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def _item(n: Notification) -> NotificationItem:
    return NotificationItem(
        id=n.id,
        type=n.type_key,
        title=n.title,
        body=n.body,
        created_at=n.created_at,
        read_at=n.read_at,
    )


def _setting(state: TypeState) -> TypeSetting:
    return TypeSetting(
        type=state.key,
        title=state.title,
        in_app=state.in_app,
        email=state.email,
        user_mutable=state.user_mutable,
    )


def build_public_router(infra: Infrastructure, services: ApiServices) -> APIRouter:
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
        response: Response,
        unread_only: Annotated[bool, Query()] = False,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> NotificationPage:
        page = await services.queries.page(
            user.user_id, unread_only=unread_only, cursor=cursor, limit=limit
        )
        response.headers["Cache-Control"] = PRIVATE
        return NotificationPage(
            items=[_item(n) for n in page.items], next_cursor=page.next_cursor, unread=page.unread
        )

    @router.get(
        "/notifications/unread-count",
        response_model=UnreadCount,
        summary="Число непрочитанных",
        responses=problem_responses(401, 403),
    )
    async def unread_count(user: User, response: Response) -> UnreadCount:
        count = await services.queries.unread_count(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return UnreadCount(count=count)

    @router.post(
        "/notifications/read-all",
        response_model=MarkedRead,
        summary="Прочитать все",
        description="Идемпотентно: повтор отмечает 0.",
        responses=problem_responses(401, 403),
    )
    async def read_all(user: User, response: Response) -> MarkedRead:
        marked = await services.queries.mark_all_read(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return MarkedRead(marked=marked)

    @router.post(
        "/notifications/{notification_id}/read",
        status_code=204,
        summary="Отметить прочитанным",
        description="Идемпотентно. Чужое или несуществующее - 404 `notifications.not_found`.",
        responses=problem_responses(401, 403, 404),
    )
    async def read_one(notification_id: IdPath, user: User, response: Response) -> None:
        await services.queries.mark_read(user.user_id, notification_id)
        response.headers["Cache-Control"] = PRIVATE

    @router.get(
        "/notification-settings",
        response_model=NotificationSettings,
        summary="Настройки уведомлений",
        description="Типы из конфига с текущими каналами игрока.",
        responses=problem_responses(401, 403),
    )
    async def settings(user: User, response: Response) -> NotificationSettings:
        states = await services.preferences.list(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return NotificationSettings(types=[_setting(s) for s in states])

    @router.put(
        "/notification-settings/{type_key}",
        response_model=TypeSetting,
        summary="Изменить настройку типа",
        description=(
            "Идемпотентно по природе операции. Неизвестный тип - 404 "
            "`notifications.type_not_found`; тип с `user_mutable = false` - 422 "
            "`notifications.type_not_mutable`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def set_setting(
        type_key: TypePath, body: TypeSettingRequest, user: User, response: Response
    ) -> TypeSetting:
        state = await services.preferences.set_type(
            user.user_id, type_key, in_app=body.in_app, email=body.email
        )
        response.headers["Cache-Control"] = PRIVATE
        return _setting(state)

    @router.get(
        "/email-subscription",
        response_model=EmailSubscription,
        summary="Согласие на рассылку",
        responses=problem_responses(401, 403),
    )
    async def email_subscription(user: User, response: Response) -> EmailSubscription:
        found = await services.preferences.subscription(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return EmailSubscription(subscribed=found.subscribed, changed_at=found.changed_at)

    @router.put(
        "/email-subscription",
        response_model=EmailSubscription,
        summary="Дать или отозвать согласие на рассылку",
        description="По умолчанию согласия нет; рассылка без согласия не уходит.",
        responses=problem_responses(401, 403, 422),
    )
    async def set_email_subscription(
        body: EmailSubscriptionRequest, user: User, response: Response
    ) -> EmailSubscription:
        found = await services.preferences.set_subscription(
            user.user_id, subscribed=body.subscribed
        )
        response.headers["Cache-Control"] = PRIVATE
        return EmailSubscription(subscribed=found.subscribed, changed_at=found.changed_at)

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
        responses=problem_responses(422, 429, 503),
    )
    async def unsubscribe(body: UnsubscribeRequest, request: Request, response: Response) -> None:
        client_ip = request.client.host if request.client else UNKNOWN_CLIENT
        await services.unsubscribe.execute(body.token, client_ip=client_ip)
        response.headers["Cache-Control"] = PRIVATE

    @router.post(
        "/devices",
        response_model=Device,
        status_code=201,
        summary="Зарегистрировать устройство для Push",
        description=(
            "Push пока не отправляется (порт без реализации); повтор токена - то же устройство."
        ),
        responses=problem_responses(401, 403, 422),
    )
    async def register_device(
        body: DeviceRequest, user: User, key: IdempotencyKey, response: Response
    ) -> Device:
        device = await services.devices.register(user.user_id, body.platform, body.token)
        response.headers["Cache-Control"] = PRIVATE
        return Device(
            id=device.id,
            platform=cast(Platform, device.platform),  # CHECK в БД и схема запроса
            registered_at=device.registered_at,
        )

    @router.delete(
        "/devices/{device_id}",
        status_code=204,
        summary="Удалить устройство",
        description="Идемпотентно; чужое устройство - 404.",
        responses=problem_responses(401, 403, 404),
    )
    async def delete_device(device_id: IdPath, user: User, response: Response) -> None:
        await services.devices.delete(user.user_id, device_id)
        response.headers["Cache-Control"] = PRIVATE

    return router
