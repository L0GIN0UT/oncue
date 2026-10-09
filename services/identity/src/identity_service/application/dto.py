"""Входные и выходные структуры use-cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from identity_service.domain import ConsentRequirement


@dataclass(frozen=True, slots=True)
class Actor:
    """Кто выполняет действие (из проверенного access-токена)."""

    user_id: UUID
    roles: frozenset[str]
    perms: frozenset[str]
    session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RegisterCommand:
    """Регистрация по никнейму, e-mail и паролю."""

    nickname: str
    email: str
    password: str
    consents: tuple[ConsentRequirement, ...]
    display_name: str | None = None
    locale: str | None = None
    ip: str | None = None


@dataclass(frozen=True, slots=True)
class LinkedAccountView:
    """Привязанный аккаунт для профиля (без внешнего идентификатора)."""

    provider: str
    display: str | None
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class UserView:
    """Данные пользователя для владельца аккаунта и админки."""

    id: UUID
    nickname: str
    email: str
    email_verified: bool
    display_name: str
    locale: str | None
    status: str
    roles: tuple[str, ...]
    perms: tuple[str, ...]
    created_at: datetime
    linked_accounts: tuple[LinkedAccountView, ...] = ()
    consents: tuple[ConsentRequirement, ...] = ()
