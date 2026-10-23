"""Порты application: хранилище (UnitOfWork), realtime, e-mail, Push, лимитер, очередь."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from gv_db.idempotency import IdempotencyStore
from notifications_service.domain.notification import (
    Device,
    Notification,
    Subscription,
    TypePreference,
)


@dataclass(frozen=True, slots=True)
class Broadcast:
    """Рассылка администрации."""

    id: UUID
    subject: str
    body: str
    status: str
    recipients: int
    delivered: int
    failed: int
    created_at: datetime
    created_by: UUID


@dataclass(frozen=True, slots=True)
class AdminAction:
    """Запись аудита (причина живёт только здесь)."""

    id: UUID
    actor_id: UUID
    action: str
    target: str
    reason: str
    at: datetime
    details: dict[str, Any]  # Any: JSON-детали аудита


@dataclass(frozen=True, slots=True)
class Recipient:
    """Получатель письма: идентификатор и адрес (адрес не хранится и не логируется)."""

    user_id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Письмо."""

    to: str
    subject: str
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)
    """Дополнительные заголовки (``List-Unsubscribe`` и др.); значения без CR/LF."""


class PermanentEmailError(Exception):
    """Адрес отвергнут окончательно (получатель отклонён SMTP-сервером): повтор бесполезен."""


class NotificationRepository(Protocol):
    """Уведомления."""

    async def add(self, notification: Notification) -> bool:
        """Сохранить; ``False`` - запись с таким ``(user_id, dedup_key)`` уже есть."""
        ...

    async def page(
        self,
        user_id: UUID,
        *,
        unread_only: bool,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Notification]:
        """Новые первыми; ``after`` - позиция последнего элемента предыдущей страницы."""
        ...

    async def unread_count(self, user_id: UUID) -> int:
        """Число непрочитанных."""
        ...

    async def mark_read(self, user_id: UUID, notification_id: UUID, now: datetime) -> bool:
        """Отметить прочитанным; ``False`` - нет такого уведомления у этого игрока."""
        ...

    async def mark_all_read(self, user_id: UUID, now: datetime) -> int:
        """Отметить все; вернуть число изменённых."""
        ...


class PreferenceRepository(Protocol):
    """Настройки по типам."""

    async def all(self, user_id: UUID) -> dict[str, TypePreference]:
        """Явно сохранённые настройки игрока по ключам типов."""
        ...

    async def save(self, user_id: UUID, type_key: str, pref: TypePreference, now: datetime) -> None:
        """Сохранить (upsert)."""
        ...


class SubscriptionRepository(Protocol):
    """Согласие на рассылку."""

    async def get(self, user_id: UUID) -> Subscription | None:
        """Текущее состояние или ``None`` (согласия не было)."""
        ...

    async def save(self, user_id: UUID, *, subscribed: bool, now: datetime) -> None:
        """Сохранить состояние (upsert); ``changed_at`` меняется только при смене значения."""
        ...

    async def unsubscribe(self, user_id: UUID, now: datetime) -> None:
        """Отозвать согласие (нет записи или уже отозвано - ничего не меняет)."""
        ...

    async def count_subscribed(self) -> int:
        """Сколько игроков дали согласие."""
        ...

    async def undelivered_after(
        self, broadcast_id: UUID, after: UUID | None, limit: int
    ) -> list[UUID]:
        """Подписчики без записи о доставке рассылки, порцией по возрастанию ``user_id``."""
        ...


class DeviceRepository(Protocol):
    """Устройства Push."""

    async def register(
        self, device_id: UUID, user_id: UUID, platform: str, token_hash: str, now: datetime
    ) -> Device:
        """Зарегистрировать; тот же токен - то же устройство (с переходом к новому владельцу)."""
        ...

    async def delete(self, user_id: UUID, device_id: UUID, now: datetime) -> bool:
        """Удалить своё устройство (мягко, ``deleted_at``); повтор тоже ``True``.

        ``False`` - устройства нет или оно чужое.
        """
        ...


class BroadcastRepository(Protocol):
    """Рассылки и факты отправки."""

    async def add(self, broadcast: Broadcast) -> None:
        """Создать."""
        ...

    async def get(self, broadcast_id: UUID, *, lock: bool = False) -> Broadcast | None:
        """Найти (``lock`` - ``FOR UPDATE``)."""
        ...

    async def page(self, *, after: tuple[datetime, UUID] | None, limit: int) -> list[Broadcast]:
        """Новые первыми."""
        ...

    async def set_status(self, broadcast_id: UUID, status: str) -> None:
        """Сменить статус."""
        ...

    async def add_counts(self, broadcast_id: UUID, *, delivered: int, failed: int) -> None:
        """Увеличить счётчики отправленных и окончательно отвергнутых писем."""
        ...

    async def mark_failed(self, broadcast_id: UUID, user_id: UUID) -> None:
        """Пометить доставку окончательно неудавшейся (запись остаётся, повтора не будет)."""
        ...

    async def claim(self, broadcast_id: UUID, user_id: UUID, now: datetime) -> bool:
        """Занять отправку получателю; ``False`` - уже отправлено/занято."""
        ...

    async def release(self, broadcast_id: UUID, user_id: UUID) -> None:
        """Снять занятие (отправка не удалась - разрешить повтор)."""
        ...


class AuditRepository(Protocol):
    """Аудит admin-действий."""

    async def record(self, action: AdminAction) -> None:
        """Записать."""
        ...


class UnitOfWork(Protocol):
    """Транзакция use-case: без явного ``commit`` изменения откатываются."""

    # Свойства (только чтение): реализация хранит конкретные репозитории, протокол их не меняет.
    @property
    def notifications(self) -> NotificationRepository: ...

    @property
    def preferences(self) -> PreferenceRepository: ...

    @property
    def subscriptions(self) -> SubscriptionRepository: ...

    @property
    def devices(self) -> DeviceRepository: ...

    @property
    def broadcasts(self) -> BroadcastRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def idempotency(self) -> IdempotencyStore: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Зафиксировать транзакцию."""
        ...


class UnitOfWorkFactory(Protocol):
    """Фабрика UoW: каждый вызов - новая транзакция (или SAVEPOINT в транзакции consumer)."""

    def __call__(self) -> UnitOfWork: ...


class RealtimePublisher(Protocol):
    """Публикация в личный канал Centrifugo. Ошибки не пробрасывает (лог и метрика)."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:  # Any: JSON
        """Опубликовать сообщение в канал."""
        ...


class EmailSender(Protocol):
    """Отправка письма. Сбой - исключение (воркер решает, что делать)."""

    async def send(self, message: EmailMessage) -> None:
        """Отправить."""
        ...


class EmailDirectory(Protocol):
    """Адреса получателей. Адреса принадлежат identity, notifications их не хранит."""

    async def resolve(self, user_ids: list[UUID]) -> list[Recipient]:
        """Адреса для игроков; без адреса или с неподтверждённым - пропускаются."""
        ...


class PushSender(Protocol):
    """Push-уведомление на устройства (реализации в 5b нет)."""

    async def send(self, user_id: UUID, title: str, body: str) -> None:
        """Отправить."""
        ...


class AttemptLimiter(Protocol):
    """Лимит попыток по субъекту (IP). Недоступное хранилище - fail-closed."""

    async def hit(self, subject: str) -> int:
        """Учесть попытку и вернуть её номер в окне.

        Raises:
            DependencyUnavailableError: хранилище недоступно (503 + ``Retry-After``).
        """
        ...
