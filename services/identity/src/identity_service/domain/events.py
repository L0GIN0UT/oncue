"""Доменные события identity. Схемы — ``contracts/events/identity/*.v1.json``.

Все события агрегата ``user`` (топик ``identity.user.v1``, ключ партиции — ``user_id``).
ПДн (e-mail, IP, внешние идентификаторы) в события не попадают — минимизация данных.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class _UserEvent(DomainEvent):
    aggregate_type: ClassVar[str] = "user"

    user_id: UUID
    version: int


@dataclass(frozen=True, kw_only=True)
class UserRegistered(_UserEvent):
    """Пользователь зарегистрирован."""

    nickname: str
    display_name: str
    locale: str | None


@dataclass(frozen=True, kw_only=True)
class UserProfileBasicsChanged(_UserEvent):
    """Изменились публичные данные профиля."""

    nickname: str
    display_name: str


@dataclass(frozen=True, kw_only=True)
class UserRolesChanged(_UserEvent):
    """Роли и итоговые права (полный снимок, списки отсортированы)."""

    roles: tuple[str, ...]
    perms: tuple[str, ...]
    actor_id: UUID | None
    reason: str | None


@dataclass(frozen=True, kw_only=True)
class UserDeleted(_UserEvent):
    """Аккаунт удалён."""

    deleted_at: datetime


@dataclass(frozen=True, kw_only=True)
class ExternalAccountLinked(_UserEvent):
    """Привязан внешний сервис (без внешнего идентификатора)."""

    provider: str


@dataclass(frozen=True, kw_only=True)
class ExternalAccountUnlinked(_UserEvent):
    """Отвязан внешний сервис."""

    provider: str
