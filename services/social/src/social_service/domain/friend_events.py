"""События друзей (контракты ``contracts/events/social/*.v1.json``, топик ``social.social.v1``).

Поля совпадают со схемами один в один. Тип агрегата у всех событий social - ``social``
(топик ``social.social.v1``, ключ партиции - ``aggregate_id``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class FriendRequested(DomainEvent):
    """Создана исходящая заявка. ``aggregate_id`` - ``request_id``."""

    aggregate_type: ClassVar[str] = "social"

    request_id: UUID
    from_user_id: UUID
    from_nickname: str
    to_user_id: UUID
    at: datetime


@dataclass(frozen=True, kw_only=True)
class FriendAdded(DomainEvent):
    """Дружба создана. ``aggregate_id`` - ``user_a`` (``user_a < user_b``)."""

    aggregate_type: ClassVar[str] = "social"

    user_a: UUID
    user_b: UUID
    requester_id: UUID
    accepter_id: UUID
    accepter_nickname: str
    at: datetime


@dataclass(frozen=True, kw_only=True)
class FriendRemoved(DomainEvent):
    """Дружба разорвана. ``aggregate_id`` - ``user_a``."""

    aggregate_type: ClassVar[str] = "social"

    user_a: UUID
    user_b: UUID
    removed_by: UUID
    reason: str  # removed | blocked | account_deleted
    at: datetime


@dataclass(frozen=True, kw_only=True)
class UserBlocked(DomainEvent):
    """Игрок заблокировал другого. ``aggregate_id`` - ``blocker_id``."""

    aggregate_type: ClassVar[str] = "social"

    blocker_id: UUID
    blocked_id: UUID
    at: datetime


@dataclass(frozen=True, kw_only=True)
class RoomInviteSent(DomainEvent):
    """Друг приглашён в комнату. ``aggregate_id`` - ``invite_id``."""

    aggregate_type: ClassVar[str] = "social"

    invite_id: UUID
    from_user_id: UUID
    from_nickname: str
    to_user_id: UUID
    room_code: str
    at: datetime
