"""Доменные события rating (контракты ``contracts/events/rating/*.v1.json``).

Поля совпадают со схемами один в один (``additionalProperties: false``). Все события —
в одном топике ``rating.rating.v1`` (``aggregate_type = "rating"``), порядок по ключу:
``RatingChanged`` — по ``user_id``, сезонные и ladder-события — по своему ``aggregate_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent

RATING_AGGREGATE = "rating"


@dataclass(frozen=True, kw_only=True)
class RatingChanged(DomainEvent):
    """Рейтинг игрока изменился после рейтингового матча (``aggregate_id`` = user_id)."""

    aggregate_type: ClassVar[str] = RATING_AGGREGATE

    user_id: UUID
    ladder: str
    format: str
    season_id: UUID
    match_id: UUID
    version: int
    old: int
    new: int
    delta: int
    tier: str | None
    old_tier: str | None
    calibrating: bool
    calibration_left: int
    matches_played: int
    outcome: str
    leaver_penalty: int
    at: datetime


@dataclass(frozen=True, kw_only=True)
class LadderPublished(DomainEvent):
    """Опубликована версия конфига ladder-а (``aggregate_id`` = ключ ladder-а)."""

    aggregate_type: ClassVar[str] = RATING_AGGREGATE

    ladder: str
    version: int
    domain: str
    formats: tuple[str, ...]
    matchmaking: dict[str, Any]
    published_at: datetime


@dataclass(frozen=True, kw_only=True)
class SeasonStarted(DomainEvent):
    """Начался сезон (``aggregate_id`` = season_id); ``soft_reset`` — null у первого."""

    aggregate_type: ClassVar[str] = RATING_AGGREGATE

    season_id: UUID
    ladder: str
    number: int
    ladder_version: int
    starts_at: datetime
    ends_at: datetime
    soft_reset: dict[str, Any] | None


@dataclass(frozen=True, kw_only=True)
class SeasonFinished(DomainEvent):
    """Сезон закрыт (``aggregate_id`` = season_id)."""

    aggregate_type: ClassVar[str] = RATING_AGGREGATE

    season_id: UUID
    ladder: str
    number: int
    finished_at: datetime
    closed_by: str
    players_count: int
    standings_ref: str


@dataclass(frozen=True, kw_only=True)
class StandingEntry:
    """Строка итоговой таблицы сезона."""

    user_id: UUID
    place: int
    rating: int


@dataclass(frozen=True, kw_only=True)
class SeasonStandingsFinalized(DomainEvent):
    """Пачка верха итоговой таблицы (``aggregate_id`` = ``{season_id}:{chunk}``)."""

    aggregate_type: ClassVar[str] = RATING_AGGREGATE

    season_id: UUID
    ladder: str
    number: int
    finished_at: datetime
    chunk: int
    chunks_total: int
    entries: tuple[StandingEntry, ...]


@dataclass(frozen=True, kw_only=True)
class WeeklyTopEntry:
    """Строка топа недели."""

    user_id: UUID
    delta: int
    place: int


@dataclass(frozen=True, kw_only=True)
class WeeklyTopFinalized(DomainEvent):
    """Топ недели закрыт (``aggregate_id`` = ``{ladder}:{week_start}``)."""

    aggregate_type: ClassVar[str] = RATING_AGGREGATE

    ladder: str
    season_id: UUID
    week_start: str
    top: tuple[WeeklyTopEntry, ...]
