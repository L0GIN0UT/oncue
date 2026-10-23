"""Бусты опыта: активный, очередь, трата (ADR 0044).

Буст - ``(множитель, осталось матчей)``. Бусты не складываются: действует самый ранний с
остатком, остальные ждут в очереди. Очередь ограничена ``wheel.max_boost_queue``; новый буст при
полной очереди не выдаётся (приз спина заменяется на пустой). Множитель - только к опыту онлайна.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Boost:
    """Буст игрока."""

    id: UUID
    user_id: UUID
    multiplier: Decimal
    matches_left: int
    queued_at: datetime


def live(boosts: Sequence[Boost]) -> list[Boost]:
    """Бусты с остатком в порядке очереди: ранний первым (при равенстве - по id)."""
    return sorted((b for b in boosts if b.matches_left > 0), key=lambda b: (b.queued_at, b.id))


def active(boosts: Sequence[Boost]) -> Boost | None:
    """Действующий буст - самый ранний с остатком."""
    ordered = live(boosts)
    return ordered[0] if ordered else None


def queued(boosts: Sequence[Boost]) -> int:
    """Сколько бустов ждёт после действующего."""
    return max(0, len(live(boosts)) - 1)


def can_enqueue(boosts: Sequence[Boost], max_queue: int) -> bool:
    """Влезет ли новый буст: в очереди (после действующего) не больше ``max_queue``."""
    return queued(boosts) < max_queue or not live(boosts)
