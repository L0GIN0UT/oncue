"""Схемы API notifications (план этапа 5, §9.5; ADR 0047).

Типы уведомлений, заголовки и тексты приходят из конфига
(``contracts/notifications/notification_types.v1.json``), в коде их нет.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from gv_service import Reason

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
Platform = Literal["web", "ios", "android"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationItem(BaseModel):
    """Уведомление игрока: текст уже собран по шаблону и локали."""

    id: UUID
    type: Key
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None = Field(description="null - не прочитано")


class NotificationPage(BaseModel):
    """Страница уведомлений (курсор), новые первыми."""

    items: list[NotificationItem]
    next_cursor: Cursor | None
    unread: int = Field(ge=0)


class UnreadCount(BaseModel):
    """Число непрочитанных (бейдж колокольчика)."""

    count: int = Field(ge=0)


class MarkedRead(BaseModel):
    """Сколько уведомлений отмечено прочитанными."""

    marked: int = Field(ge=0)


class TypeSetting(BaseModel):
    """Настройка типа уведомления."""

    type: Key
    title: str = Field(description="Название типа для экрана настроек (конфиг, локаль игрока)")
    in_app: bool
    email: bool
    user_mutable: bool = Field(description="false - тип нельзя отключить")


class NotificationSettings(BaseModel):
    """Настройки по типам."""

    types: list[TypeSetting]


class TypeSettingRequest(_Request):
    """Включить или отключить каналы типа."""

    in_app: bool
    email: bool


class EmailSubscription(BaseModel):
    """Согласие на информационную рассылку от администрации."""

    subscribed: bool
    changed_at: datetime | None


class EmailSubscriptionRequest(_Request):
    """Дать или отозвать согласие на рассылку."""

    subscribed: bool


class UnsubscribeRequest(_Request):
    """Отписка по токену из письма (страница отписки)."""

    # Без ограничений длины намеренно: формат проверяется только после лимита по IP, мусор
    # тоже считается попыткой; негодный токен - тот же 422, что и неизвестный.
    token: str = Field(description="Непрозрачный токен из ссылки в письме")


class DeviceRequest(_Request):
    """Регистрация устройства для Push (реализация Push - после релиза)."""

    platform: Platform
    token: str = Field(min_length=8, max_length=4096)


class Device(BaseModel):
    """Зарегистрированное устройство."""

    id: UUID
    platform: Platform
    registered_at: datetime


class BroadcastRequest(_Request):
    """Информационная рассылка подписчикам."""

    subject: str = Field(
        min_length=1,
        max_length=150,
        pattern=r"^[^\x00-\x1f\x7f]+$",
        description="Тема письма: без управляющих символов (защита заголовков письма)",
    )
    body: str = Field(min_length=1, max_length=5000)
    reason: Reason


class Broadcast(BaseModel):
    """Рассылка."""

    id: UUID
    subject: str
    status: Literal["queued", "sending", "sent", "failed"]
    recipients: int = Field(ge=0, description="Подписчиков на момент постановки")
    created_at: datetime
    created_by: UUID


class BroadcastPage(BaseModel):
    """Страница рассылок (курсор)."""

    items: list[Broadcast]
    next_cursor: Cursor | None
