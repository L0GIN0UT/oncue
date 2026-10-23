"""Схемы публичного API rating (план этапа 4, §2.5; ADR 0037, 0039) — контракт для bff и фронтенда.

Звание и рейтинг скрыты, пока игрок в калибровке: в ответе ``rating`` и ``tier`` равны
``null``, а ``calibration_left`` показывает, сколько матчей осталось (решение владельца 4b).
Тексты звания — ключи i18n (``title_key``), числа порогов и длина калибровки — из конфига
ladder-а, в коде значений нет.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

LadderKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
# Форматы очереди задаёт конфиг ladder-а (`formats`), в схеме — только форма ключа.
QueueFormat = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Domain = Literal["music", "film"]
Outcome = Literal["win", "loss", "draw"]


class TierView(BaseModel):
    """Звание ladder-а: семейство и градация внутри него."""

    key: str = Field(description="Ключ из конфига ladder-а, например `silver_2`")
    family: str = Field(description="Семейство звания; набор семейств задаёт конфиг ladder-а")
    step: int | None = Field(ge=1, description="Градация (1, 2, 3); null — без градации")
    title_key: str = Field(description="Ключ i18n названия")
    below: int | None = Field(
        description="Звание действует при рейтинге строго ниже; null — высшее"
    )


class SeasonView(BaseModel):
    """Сезон ladder-а."""

    season_id: UUID
    number: int = Field(ge=1)
    starts_at: datetime
    ends_at: datetime = Field(description="«Осталось X д.» считает клиент по этому полю")


class LadderView(BaseModel):
    """Ladder (в 4b — по одному на домен) с действующим сезоном."""

    key: LadderKey
    domain: Domain
    formats: list[QueueFormat]
    version: int = Field(ge=1, description="Версия опубликованного конфига")
    calibration_matches: int = Field(ge=0, description="Длина калибровки, матчей")
    tiers: list[TierView]
    season: SeasonView | None = Field(description="null — между сезонами")


class LadderList(BaseModel):
    """Опубликованные ladder-ы."""

    items: list[LadderView]


class RatingView(BaseModel):
    """Рейтинг игрока в ladder-е в текущем сезоне."""

    ladder: LadderKey
    season_id: UUID
    rating: int | None = Field(description="null, пока идёт калибровка (число скрыто)")
    tier: TierView | None = Field(description="null, пока идёт калибровка")
    next_tier_at: int | None = Field(
        default=None, description="Рейтинг следующего звания; null — высшее или калибровка"
    )
    calibrating: bool
    calibration_left: int = Field(ge=0, description="Сколько матчей осталось до звания")
    matches_played: int = Field(ge=0)
    place: int | None = Field(default=None, ge=1, description="Место в лидерборде; null — вне его")


class MyRatings(BaseModel):
    """Рейтинги пользователя по ladder-ам (``GET /v1/me/ratings``, ``/users/{id}/ratings``)."""

    items: list[RatingView]


class LeaderboardEntry(BaseModel):
    """Строка лидерборда (ник — из проекции профиля, ПДн не отдаются)."""

    place: int = Field(ge=1)
    user_id: UUID
    nickname: str
    rating: int
    tier: TierView
    matches_played: int = Field(ge=0)


class LeaderboardPage(BaseModel):
    """Страница лидерборда: keyset-курсор или окно вокруг пользователя (``around=me``)."""

    ladder: LadderKey
    season_id: UUID
    items: list[LeaderboardEntry]
    next_cursor: str | None = None
    me: LeaderboardEntry | None = Field(
        default=None, description="Строка пользователя; только с токеном и вне калибровки"
    )


class WeeklyEntry(BaseModel):
    """Строка топа недели."""

    place: int = Field(ge=1)
    user_id: UUID
    nickname: str
    delta: int = Field(description="Прирост рейтинга с понедельника")


class WeeklyTop(BaseModel):
    """Топ недели ladder-а (прирост с понедельника в зоне конфига, ADR 0039)."""

    ladder: LadderKey
    season_id: UUID
    week_start: date
    items: list[WeeklyEntry]
    me: WeeklyEntry | None = None


class HistoryItem(BaseModel):
    """Изменение рейтинга за матч."""

    match_id: UUID
    at: datetime
    format: QueueFormat
    outcome: Outcome
    old: int | None = Field(description="null в калибровке: числа скрыты")
    new: int | None
    delta: int | None
    tier: TierView | None


class HistoryPage(BaseModel):
    """История рейтинга пользователя, новые первыми."""

    items: list[HistoryItem]
    next_cursor: str | None = None


class MatchChange(BaseModel):
    """Дельта одного игрока за матч (для итога, ``GET /v1/matches/{id}/changes``)."""

    user_id: UUID
    ladder: LadderKey
    format: QueueFormat
    outcome: Outcome
    old: int | None = Field(description="null в калибровке")
    new: int | None
    delta: int | None
    old_tier: TierView | None
    tier: TierView | None
    calibrating: bool
    calibration_left: int = Field(ge=0)
    leaver_penalty: int = Field(ge=0, description="Штраф за выход или сдачу, входит в delta")


class MatchChanges(BaseModel):
    """Дельты матча: итог опрашивает ручку до 10 с, пока консьюмер не применит событие."""

    match_id: UUID
    items: list[MatchChange]


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str
