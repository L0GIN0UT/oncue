"""Доменные события progression (контракты ``contracts/events/progression/*.v1.json``).

Поля совпадают со схемами один в один (``additionalProperties: false``). Все события — в топике
``progression.progression.v1`` (``aggregate_type = "progression"``), ключ партиции — ``user_id``
(``aggregate_id``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent

PROGRESSION_AGGREGATE = "progression"


@dataclass(frozen=True, kw_only=True)
class ExperienceGained(DomainEvent):
    """Игроку начислен опыт (одна проводка ``xp_ledger``)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    op_id: str
    amount: int
    base_amount: int
    boost_multiplier: float
    source: str
    source_ref: str
    total_xp: int
    level: int
    config_version: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class ProgressionLevelChanged(DomainEvent):
    """Уровень игрока вырос (возможно, сразу на несколько)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    old_level: int
    new_level: int
    total_xp: int
    config_version: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class CurrencyDebited(DomainEvent):
    """С игрока списаны Версы (покупка в 5b, сторно ошибочного начисления)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    op_id: str
    currency: str
    amount: int
    bucket: str
    reason: str
    source_ref: str
    reversal_of: str | None
    balance_after: int
    actor_id: UUID | None
    config_version: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class CurrencyCredited(DomainEvent):
    """Игроку начислены Версы (одна проводка ``currency_ledger``)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    op_id: str
    currency: str
    amount: int
    bucket: str
    reason: str
    source_ref: str
    balance_after: int
    actor_id: UUID | None
    config_version: int
    at: datetime


@dataclass(frozen=True, slots=True)
class RewardPayload:
    """Награда задания в событии: опыт и Версы."""

    xp: int
    verses: int


@dataclass(frozen=True, slots=True)
class PrizePayload:
    """Приз колеса в событии (по схеме ``WheelSpun``: xp, verses, boost, none)."""

    kind: str
    amount: int
    multiplier: float | None
    matches: int | None


@dataclass(frozen=True, kw_only=True)
class QuestCompleted(DomainEvent):
    """Задание выполнено, награда выдана (идемпотентно по игроку, заданию и периоду)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    quest_key: str
    period: str
    period_start: str
    reward: RewardPayload
    config_version: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class BadgeUnlocked(DomainEvent):
    """Игрок получил бейдж (один раз)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    badge_key: str
    config_version: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class WheelSpun(DomainEvent):
    """Игрок крутанул колесо (один спин в сутки)."""

    aggregate_type: ClassVar[str] = PROGRESSION_AGGREGATE

    user_id: UUID
    day: str
    sector_key: str
    prize: PrizePayload
    config_version: int
    at: datetime
