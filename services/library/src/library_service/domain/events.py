"""События медиатеки: топик ``library.shelf.v1``, ключ партиции — ``user_id``.

Контракты — ``contracts/events/library/ItemSaved|ItemRemoved.v1.json``. Публикуются только
при фактическом изменении (D12); ``version`` — монотонная версия медиатеки пользователя
(``user_versions``), у каждого события своя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent

AGGREGATE_TYPE = "shelf"
"""Часть имени топика ``library.shelf.v1``; ``aggregate_id`` — ``user_id`` (порядок событий
одного пользователя сохраняется партицией)."""


@dataclass(frozen=True, kw_only=True)
class _ShelfItemEvent(DomainEvent):
    aggregate_type: ClassVar[str] = AGGREGATE_TYPE

    user_id: UUID
    shelf_id: UUID
    shelf_kind: str
    shelf_key: str | None
    media_item_id: UUID
    domain: str
    source: str
    version: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class ItemSaved(_ShelfItemEvent):
    """Единица сохранена на полку (``source`` — экран)."""


@dataclass(frozen=True, kw_only=True)
class ItemRemoved(_ShelfItemEvent):
    """Единица убрана с полки (``source`` — экран, ``exclusive`` или ``shelf_deleted``)."""
