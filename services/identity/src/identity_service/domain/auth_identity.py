"""Привязанные внешние аккаунты и согласия."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from identity_service.domain.values import Provider


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    """Аккаунт внешнего сервиса, привязанный к пользователю (D2).

    Аккаунт провайдера (``provider`` + ``subject``) привязан максимум к одному пользователю;
    у пользователя — не больше одной привязки на провайдера (этап 1). Совпадение e-mail
    у провайдера и у нас **никогда** не связывает аккаунты автоматически (захват аккаунта).
    """

    user_id: UUID
    provider: Provider
    subject: str
    display: str | None
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class Consent:
    """Выданное согласие (append-only: отзыв — новой записью в будущих этапах)."""

    user_id: UUID
    kind: str
    version: str
    granted_at: datetime
