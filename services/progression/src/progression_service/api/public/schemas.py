"""Схемы публичного API progression (план этапа 5, §2.4; ADR 0042–0044) — контракт для bff.

Уровни, пороги, суммы, названия заданий, бейджей и секторов колеса приходят из версии конфига
progression (``contracts/progression/progression_config.v1.json``), в коде значений нет.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Domain = Literal["music", "film"]
Bucket = Literal["earned", "paid"]
QuestPeriod = Literal["daily", "weekly", "one_time"]
PrizeKind = Literal["xp", "verses", "boost", "item", "none"]


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


class DailyStreakView(BaseModel):
    """Серия дейлика по домену."""

    domain: Domain
    current: int = Field(ge=0, description="Подряд пройденных дней; обрывается пропуском дня")
    best: int = Field(ge=0)
    last_day: date | None = Field(description="Последний пройденный день; null — не проходил")


class BoostView(BaseModel):
    """Буст опыта (с колеса): множитель на N онлайн-матчей."""

    multiplier: float = Field(gt=1)
    matches_left: int = Field(ge=1)


class MyProgress(BaseModel):
    """Свой прогресс: уровень, опыт, серии, бусты."""

    level: int = Field(ge=1)
    max_level: int = Field(ge=1, description="Последний уровень конфига")
    total_xp: int = Field(ge=0)
    xp_in_level: int = Field(ge=0, description="Набрано внутри текущего уровня")
    xp_to_next: int | None = Field(ge=1, description="До следующего уровня; null — высший")
    solo_xp_today: int = Field(ge=0, description="Опыт с соло за сегодня")
    solo_xp_cap: int = Field(ge=0, description="Потолок опыта с соло в сутки (конфиг)")
    streaks: list[DailyStreakView]
    boost: BoostView | None = Field(description="Действующий буст; null — нет")
    boosts_queued: int = Field(ge=0, description="Бустов в очереди после действующего")
    config_version: int = Field(ge=1)


class SessionStreakView(BaseModel):
    """Серия дейлика, в которую засчитана сессия."""

    domain: Domain
    current: int = Field(ge=1)


class SessionRewards(BaseModel):
    """Награды за игровую сессию текущего пользователя (экран итогов).

    Начисление асинхронное (по событию game): пока его нет, `settled = false`, суммы нулевые —
    это не ошибка, клиент спрашивает позже. Чужая сессия неотличима от неначисленной.
    """

    session_id: UUID
    settled: bool = Field(description="Начисление за сессию уже есть")
    xp: int = Field(ge=0, description="Начисленный опыт (с бустом)")
    verses: int = Field(ge=0, description="Начисленные Версы, целые")
    level_before: int | None = Field(
        default=None, ge=1, description="Уровень до сессии; null — опыта нет"
    )
    level_after: int | None = Field(default=None, ge=1, description="Уровень после сессии")
    streak: SessionStreakView | None = Field(
        default=None, description="Серия дейлика на день сессии; null — не дейлик"
    )


class BadgeRef(BaseModel):
    """Бейдж в публичном профиле."""

    key: Key
    title: str
    unlocked_at: datetime


class PublicProgress(BaseModel):
    """Публичный прогресс другого игрока (профиль). Скрытие — поле модели, ручка в 5c."""

    user_id: UUID
    level: int = Field(ge=1)
    total_xp: int = Field(ge=0)
    badges: list[BadgeRef]


class LedgerEntryView(BaseModel):
    """Проводка кошелька."""

    op_id: str
    direction: Literal["credit", "debit"]
    amount: int = Field(ge=1)
    bucket: Bucket
    reason: Key
    at: datetime


class MyWallet(BaseModel):
    """Свой кошелёк: Версы по бакетам и последние проводки."""

    currency: Literal["verses"] = "verses"
    earned: int = Field(ge=0, description="Заработаны игрой")
    paid: int = Field(ge=0, description="Куплены (биллинг выключен — 0)")
    total: int = Field(ge=0)
    recent: list[LedgerEntryView] = Field(description="Последние проводки, новые первыми")


class Reward(BaseModel):
    """Награда: опыт и Версы."""

    xp: int = Field(ge=0)
    verses: int = Field(ge=0)


class QuestView(BaseModel):
    """Задание игрока в текущем периоде."""

    key: Key
    title: str
    description: str
    target: int = Field(ge=1)
    progress: int = Field(ge=0)
    completed: bool
    reward: Reward


class QuestPeriodView(BaseModel):
    """Задания одного периода."""

    period: QuestPeriod
    period_start: date
    period_end: date | None = Field(description="null у разовых")
    items: list[QuestView]


class MyQuests(BaseModel):
    """Свои задания: дневные, недельные, разовые."""

    daily: QuestPeriodView
    weekly: QuestPeriodView
    one_time: QuestPeriodView


class BadgeView(BaseModel):
    """Бейдж со своим прогрессом."""

    key: Key
    title: str
    description: str
    target: int = Field(ge=1)
    progress: int = Field(ge=0)
    unlocked_at: datetime | None = Field(description="null — не получен")


class MyBadges(BaseModel):
    """Свои бейджи, полученные и нет."""

    items: list[BadgeView]


class PrizeView(BaseModel):
    """Приз сектора колеса."""

    kind: PrizeKind
    amount: int = Field(ge=0, description="Опыт или Версы; у boost, item и none — 0")
    multiplier: float | None = Field(gt=1, description="Только boost")
    matches: int | None = Field(ge=1, description="Только boost")
    item_key: Key | None = Field(description="Только item")


class WheelSectorView(BaseModel):
    """Сектор колеса; шанс публикуется игроку."""

    key: Key
    title: str
    weight: int = Field(ge=1)
    chance: float = Field(ge=0, le=1, description="weight / сумма весов")
    prize: PrizeView


class SpinResult(BaseModel):
    """Результат спина за сутки."""

    day: date
    sector_key: Key
    prize: PrizeView
    spun_at: datetime
    repeated: bool = Field(description="true — спин за эти сутки уже был, возвращён тот же")


class WheelView(BaseModel):
    """Колесо: секторы с шансами и состояние на сегодня."""

    enabled: bool
    sectors: list[WheelSectorView]
    day: date = Field(description="Текущие сутки в `progression.timezone`")
    today: SpinResult | None = Field(description="Спин за сегодня; null — ещё не крутил")
    next_spin_at: datetime | None = Field(description="Когда обновится; null — можно крутить")
    config_version: int = Field(ge=1)
