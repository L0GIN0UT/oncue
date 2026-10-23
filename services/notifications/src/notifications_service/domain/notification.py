"""Уведомление игрока и настройки по типам."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Candidate:
    """Результат маппинга события: что и кому показать (ещё не сохранено)."""

    user_id: UUID
    type_key: str
    dedup_key: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class Notification:
    """Сохранённое уведомление."""

    id: UUID
    user_id: UUID
    type_key: str
    dedup_key: str
    title: str
    body: str
    created_at: datetime
    read_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TypePreference:
    """Каналы типа, выбранные игроком."""

    in_app: bool
    email: bool


@dataclass(frozen=True, slots=True)
class Subscription:
    """Согласие на информационную рассылку."""

    subscribed: bool
    changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class Device:
    """Устройство для Push."""

    id: UUID
    platform: str
    registered_at: datetime
