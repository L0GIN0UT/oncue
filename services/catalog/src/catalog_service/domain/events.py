"""Доменные события каталога. Схемы — ``contracts/events/catalog/*.v1.json``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from gv_core.domain import DomainEvent

MEDIA_ITEM_AGGREGATE = "media_item"


@dataclass(frozen=True, kw_only=True)
class _SnapshotEvent(DomainEvent):
    """Событие с полным снимком единицы (``MediaItemCreated``/``Updated``)."""

    aggregate_type: ClassVar[str] = MEDIA_ITEM_AGGREGATE

    snapshot: Mapping[str, Any]

    def payload(self) -> dict[str, Any]:
        """Снимок как есть: он уже в форме контракта."""
        return dict(self.snapshot)


@dataclass(frozen=True, kw_only=True)
class MediaItemCreated(_SnapshotEvent):
    """Создана единица."""


@dataclass(frozen=True, kw_only=True)
class MediaItemUpdated(_SnapshotEvent):
    """Изменены данные единицы; ``changed_fields`` — какие именно."""

    changed_fields: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        """Снимок и список изменённых полей."""
        return {**self.snapshot, "changed_fields": list(self.changed_fields)}


@dataclass(frozen=True, kw_only=True)
class MediaItemBlocked(DomainEvent):
    """Единица заблокирована модератором."""

    aggregate_type: ClassVar[str] = MEDIA_ITEM_AGGREGATE

    media_item_id: str
    version: int
    reason_code: str
    actor_id: str | None


@dataclass(frozen=True, kw_only=True)
class MediaItemUnblocked(DomainEvent):
    """Блокировка снята."""

    aggregate_type: ClassVar[str] = MEDIA_ITEM_AGGREGATE

    media_item_id: str
    version: int
    actor_id: str | None
