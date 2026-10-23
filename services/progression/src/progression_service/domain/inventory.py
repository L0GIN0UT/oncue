"""Инвентарь: что игрок получил, откуда и виден ли предмет сейчас (ADR 0045)."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from progression_service.domain.shop import Item


class Source(StrEnum):
    """Откуда предмет (значения - как в схеме ответа и в CHECK таблицы)."""

    PURCHASE = "purchase"
    LEVEL = "level"
    WHEEL = "wheel"
    PROMO = "promo"
    PACK = "pack"
    ADMIN = "admin"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class InventoryItem:
    """Строка инвентаря (PK ``(user_id, item_id)``).

    ``expires_at`` и ``convert_condition`` - поля 5d; в 5b всегда ``None``.
    """

    user_id: UUID
    item_id: UUID
    source: Source
    acquired_at: datetime
    expires_at: datetime | None
    convert_condition: str | None


def is_visible(entry: InventoryItem, now: datetime) -> bool:
    """Просроченные предметы скрыты (читаются как будто их нет)."""
    return entry.expires_at is None or entry.expires_at > now


def level_unlocks(items: Iterable[Item], *, level: int, owned_ids: Collection[UUID]) -> list[Item]:
    """Активные предметы, которые открылись уровнем и ещё не выданы."""
    return [
        i
        for i in items
        if i.active
        and i.level_required is not None
        and i.level_required <= level
        and i.id not in owned_ids
    ]
