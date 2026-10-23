"""Своя аватарка (ADR 0048): заявка, действующий аватар, ошибки.

Файл проверяет и хранит media-pipeline; здесь - право на загрузку (предмет из
``shop.custom_avatar_item_key``) и применение решений модератора по версиям ``AssetModerated``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    RateLimitedError,
)

PURPOSE = "avatar"
"""Назначение UGC-изображения в media-pipeline; события других назначений игнорируются."""

SUPERSEDED = "superseded"
"""Код прежней заявки, вытесненной новой (media-pipeline не публикует по ней события)."""

_PUBLIC_KEY = re.compile(r"^u/[a-f0-9]{32}\.webp$")


class RequestStatus(StrEnum):
    """Статус заявки."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AvatarNotEntitledError(ForbiddenError):
    """Товар «своя аватарка» не куплен (403)."""

    default_code = "progression.avatar_not_entitled"

    def __init__(self) -> None:
        super().__init__("своя аватарка не куплена")


class AvatarRateLimitedError(RateLimitedError):
    """Слишком много заявок на аватарку за окно (429, ``Retry-After`` из ``details``)."""

    default_code = "progression.avatar_rate_limited"

    def __init__(self, retry_after_s: int) -> None:
        super().__init__("слишком много заявок на аватарку", details={"retry_after": retry_after_s})


class AvatarInvalidImageError(InvalidInputError):
    """media-pipeline отклонил файл (422); причину клиенту не раскрываем."""

    default_code = "ugc.invalid_image"

    def __init__(self) -> None:
        super().__init__("файл не является допустимым изображением")


class AvatarTooLargeError(InvalidInputError):
    """Файл больше лимита (API отвечает 413)."""

    default_code = "ugc.too_large"

    def __init__(self, max_bytes: int) -> None:
        super().__init__("файл больше допустимого размера", details={"max_bytes": max_bytes})


class AvatarUnavailableError(DependencyUnavailableError):
    """media-pipeline недоступен или ответил неожиданно (503)."""

    default_code = "progression.avatar_unavailable"

    def __init__(self) -> None:
        super().__init__("загрузка аватарки временно недоступна", details={"retry_after": 5})


@dataclass(frozen=True, slots=True)
class AvatarRequest:
    """Заявка на аватарку."""

    id: UUID
    user_id: UUID
    asset_id: UUID
    status: RequestStatus
    submitted_at: datetime
    decided_at: datetime | None
    reason_code: str | None
    version: int


@dataclass(frozen=True, slots=True)
class CurrentAvatar:
    """Действующая одобренная аватарка."""

    user_id: UUID
    asset_id: UUID
    public_key: str
    version: int
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class Moderation:
    """Решение модератора из ``AssetModerated`` (только поля, нужные progression)."""

    asset_id: UUID
    owner_user_id: UUID
    status: RequestStatus
    reason_code: str | None
    public_key: str | None
    decided_at: datetime
    version: int

    def __post_init__(self) -> None:
        if self.status is RequestStatus.PENDING:
            raise ValueError("решение не может быть pending")
        if self.version < 1:
            raise ValueError("version >= 1")
        if (self.status is RequestStatus.APPROVED) != (self.public_key is not None):
            raise ValueError("public_key есть ровно у approved")
        if self.public_key is not None and not _PUBLIC_KEY.match(self.public_key):
            raise ValueError("public_key вне формата u/<hex>.webp")
