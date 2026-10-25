"""Доменные события collections (контракты ``contracts/events/collections/*.v1.json``).

Поля совпадают со схемами один в один (``additionalProperties: false``). UGC-текст
(описание) в события не попадает; название (≤ 120) и теги - необязательные поля снимка
(ADR 0067). Все события топика ``collections.collection.v1`` несут монотонную ``version``
агрегата (ADR 0028).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent

COLLECTION_AGGREGATE = "collection"
EVENT_TITLE_MAX = 120
"""Предел `title` в схеме событий (ADR 0067); название длиннее обрезается."""


@dataclass(frozen=True, kw_only=True)
class EventCustom:
    """Свои загадки единицы в событии (ADR 0029): только эмодзи."""

    emoji: str


@dataclass(frozen=True, kw_only=True)
class EventItem:
    """Единица состава в событии: позиция с 0, без пропусков."""

    media_item_id: UUID
    position: int
    custom: EventCustom | None = None


@dataclass(frozen=True, kw_only=True)
class _Snapshot(DomainEvent):
    """Полный снимок опубликованного состава (общая схема Published/Updated)."""

    aggregate_type: ClassVar[str] = COLLECTION_AGGREGATE

    collection_id: UUID
    version: int
    content_version: int
    domain: str
    kind: str
    visibility: str
    status: str
    author_id: UUID | None
    default_mode_key: str
    items: tuple[EventItem, ...]
    published_at: datetime
    title: str = ""
    tags: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        """Payload по схеме: ``custom`` у единицы без своих загадок отсутствует."""
        data = super().payload()
        data["items"] = [
            {
                "media_item_id": str(i.media_item_id),
                "position": i.position,
                **({"custom": {"emoji": i.custom.emoji}} if i.custom else {}),
            }
            for i in self.items
        ]
        return data


@dataclass(frozen=True, kw_only=True)
class CollectionPublished(_Snapshot):
    """Опубликована (впервые или новой версией) версия состава из черновика."""


@dataclass(frozen=True, kw_only=True)
class CollectionUpdated(_Snapshot):
    """Сборник изменился без публикации черновика: скрыт/показан, убрана заблокированная
    единица, обезличен автор."""


@dataclass(frozen=True, kw_only=True)
class CollectionDeleted(DomainEvent):
    """Опубликованный сборник удалён (автором или вместе с аккаунтом)."""

    aggregate_type: ClassVar[str] = COLLECTION_AGGREGATE

    collection_id: UUID
    version: int
    deleted_at: datetime


@dataclass(frozen=True, kw_only=True)
class CollectionLiked(DomainEvent):
    """Новый лайк пользователя (повтор и снятие лайка событий не публикуют, D13)."""

    aggregate_type: ClassVar[str] = COLLECTION_AGGREGATE

    collection_id: UUID
    version: int
    user_id: UUID
    likes_count: int
    liked_at: datetime
