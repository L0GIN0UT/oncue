"""Use-cases игрока: список, прочитано, настройки, согласие на рассылку, устройства."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from gv_core.clock import Clock
from notifications_service.application.cursor import decode_cursor, encode_cursor
from notifications_service.application.ports import UnitOfWorkFactory
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.errors import (
    DeviceNotFoundError,
    NotificationNotFoundError,
    TypeNotFoundError,
    TypeNotMutableError,
)
from notifications_service.domain.notification import (
    Device,
    Notification,
    Subscription,
    TypePreference,
)


@dataclass(frozen=True, slots=True)
class NotificationsPage:
    """Страница уведомлений."""

    items: list[Notification]
    next_cursor: str | None
    unread: int


@dataclass(frozen=True, slots=True)
class TypeState:
    """Тип с каналами игрока."""

    key: str
    title: str
    in_app: bool
    email: bool
    user_mutable: bool


class NotificationQueries:
    """Чтение и отметки «прочитано»; всё - только по владельцу."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow_factory
        self._clock = clock

    async def page(
        self, user_id: UUID, *, unread_only: bool, cursor: str | None, limit: int
    ) -> NotificationsPage:
        """Новые первыми, курсорная пагинация."""
        after = decode_cursor(cursor) if cursor is not None else None
        async with self._uow() as uow:
            rows = await uow.notifications.page(
                user_id, unread_only=unread_only, after=after, limit=limit + 1
            )
            unread = await uow.notifications.unread_count(user_id)
        items = rows[:limit]
        more = len(rows) > limit
        next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if more else None
        return NotificationsPage(items=items, next_cursor=next_cursor, unread=unread)

    async def unread_count(self, user_id: UUID) -> int:
        """Число непрочитанных."""
        async with self._uow() as uow:
            return await uow.notifications.unread_count(user_id)

    async def mark_read(self, user_id: UUID, notification_id: UUID) -> None:
        """Отметить прочитанным (идемпотентно).

        Raises:
            NotificationNotFoundError: нет такого уведомления у игрока (чужое - тоже 404).
        """
        async with self._uow() as uow:
            found = await uow.notifications.mark_read(user_id, notification_id, self._clock.now())
            if not found:
                raise NotificationNotFoundError
            await uow.commit()

    async def mark_all_read(self, user_id: UUID) -> int:
        """Прочитать все; повтор отмечает 0."""
        async with self._uow() as uow:
            marked = await uow.notifications.mark_all_read(user_id, self._clock.now())
            await uow.commit()
        return marked


class PreferenceService:
    """Настройки по типам и согласие на рассылку."""

    def __init__(self, uow_factory: UnitOfWorkFactory, catalog: TypeCatalog, clock: Clock) -> None:
        self._uow = uow_factory
        self._catalog = catalog
        self._clock = clock

    def _title(self, key: str) -> str:
        notification_type = self._catalog.by_key(key)
        assert notification_type is not None  # noqa: S101 — ключ взят из каталога
        return notification_type.templates[self._catalog.default_locale].title

    async def list(self, user_id: UUID) -> list[TypeState]:
        """Все типы каталога с каналами игрока (нет настройки - значения из конфига)."""
        async with self._uow() as uow:
            saved = await uow.preferences.all(user_id)
        result: list[TypeState] = []
        for t in self._catalog.types:
            pref = saved.get(t.key) if t.user_mutable else None
            result.append(
                TypeState(
                    key=t.key,
                    title=self._title(t.key),
                    in_app=pref.in_app if pref else t.channels.in_app,
                    email=pref.email if pref else t.channels.email,
                    user_mutable=t.user_mutable,
                )
            )
        return result

    async def set_type(
        self, user_id: UUID, type_key: str, *, in_app: bool, email: bool
    ) -> TypeState:
        """Включить или отключить каналы типа (идемпотентно).

        Raises:
            TypeNotFoundError: типа нет в конфиге.
            TypeNotMutableError: тип нельзя отключить.
        """
        notification_type = self._catalog.by_key(type_key)
        if notification_type is None:
            raise TypeNotFoundError
        if not notification_type.user_mutable:
            raise TypeNotMutableError
        async with self._uow() as uow:
            await uow.preferences.save(
                user_id, type_key, TypePreference(in_app=in_app, email=email), self._clock.now()
            )
            await uow.commit()
        return TypeState(
            key=type_key,
            title=self._title(type_key),
            in_app=in_app,
            email=email,
            user_mutable=True,
        )

    async def subscription(self, user_id: UUID) -> Subscription:
        """Согласие на рассылку; по умолчанию его нет."""
        async with self._uow() as uow:
            found = await uow.subscriptions.get(user_id)
        return found or Subscription(subscribed=False, changed_at=None)

    async def set_subscription(self, user_id: UUID, *, subscribed: bool) -> Subscription:
        """Дать или отозвать согласие (идемпотентно)."""
        async with self._uow() as uow:
            await uow.subscriptions.save(user_id, subscribed=subscribed, now=self._clock.now())
            await uow.commit()
            found = await uow.subscriptions.get(user_id)
        assert found is not None  # noqa: S101 — только что сохранено
        return found


class DeviceService:
    """Устройства для Push (отправки в 5b нет)."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, new_id: Callable[[], UUID]
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._new_id = new_id

    async def register(self, user_id: UUID, platform: str, token: str) -> Device:
        """Зарегистрировать устройство; тот же токен - то же устройство. Токен хранится хешем."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with self._uow() as uow:
            device = await uow.devices.register(
                self._new_id(), user_id, platform, token_hash, self._clock.now()
            )
            await uow.commit()
        return device

    async def delete(self, user_id: UUID, device_id: UUID) -> None:
        """Удалить своё устройство.

        Raises:
            DeviceNotFoundError: устройства нет или оно чужое (404). Повторное удаление своего
                уже удалённого устройства - успех (идемпотентно).
        """
        async with self._uow() as uow:
            if not await uow.devices.delete(user_id, device_id, self._clock.now()):
                raise DeviceNotFoundError
            await uow.commit()
