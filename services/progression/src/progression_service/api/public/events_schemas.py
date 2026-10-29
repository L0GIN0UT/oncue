"""Схемы публичного API 5d: сезонные события (ADR 0054-0056, ``docs/plans/stage-5d-api.md``).

Тексты и награды приходят из снимка запуска, в коде значений нет. Прогресс закрытой финальной
находки не отдаётся (``trigger.progress = null``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

EventKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
ThemeKey = Annotated[str, StringConstraints(pattern=r"^([a-z][a-z0-9_-]*)?$", max_length=32)]
RunStatus = Literal["active", "finished", "stopped"]
FindState = Literal["locked", "open", "found"]
TriggerType = Literal["server", "ui_spot"]
TriggerPeriod = Literal["day", "event"]


class EventBanner(BaseModel):
    """Активное событие для шапки и главной."""

    run_id: UUID
    key: EventKey
    title: str
    theme_key: ThemeKey = Field(description="Пустая строка - тема не меняется")
    starts_at: datetime
    ends_at: datetime


class ActiveEvent(BaseModel):
    """Ответ ``GET /v1/events/active``."""

    event: EventBanner | None


class EventRewardItem(BaseModel):
    """Основная награда за закрытие всех находок."""

    item_key: EventKey
    title: str
    image_url: str


class FindReward(BaseModel):
    """Награда находки."""

    xp: int = Field(ge=0)
    verses: int = Field(ge=0)


class FindTrigger(BaseModel):
    """Условие открытия находки."""

    type: TriggerType
    page_key: str | None = Field(description="Страница спрятанного элемента (`ui_spot`)")
    metric: str | None = Field(description="Метрика реестра (`server`)")
    progress: int | None = Field(
        description="Текущее значение метрики; у закрытой финальной находки - null"
    )
    target: int | None = Field(description="Цель метрики (`server`)")
    period: TriggerPeriod | None = Field(
        description="`day` - сброс каждые сутки, `event` - за запуск"
    )


class EventFind(BaseModel):
    """Находка события с состоянием для игрока."""

    key: EventKey
    number: int = Field(ge=1, le=20)
    title: str
    x_pct: float = Field(ge=0, le=100)
    y_pct: float = Field(ge=0, le=100)
    final: bool
    state: FindState
    diary: str = Field(description="`diary_before` до открытия, `diary_after` после")
    reward: FindReward
    play_page: str | None = Field(description="Раздел для кнопки «играть»")
    trigger: FindTrigger
    task_i18n: str | None = Field(
        default=None,
        description="Ключ i18n текста задания («что сделать»); null - задания нет",
    )
    found_at: datetime | None


class EventPage(BaseModel):
    """Страница события: карта, находки, прогресс игрока."""

    key: EventKey
    title: str
    description: str
    theme_key: ThemeKey
    status: RunStatus
    starts_at: datetime
    ends_at: datetime
    map_url: str | None
    found: int = Field(ge=0)
    total: int = Field(ge=0)
    completed: bool
    reward_item: EventRewardItem | None
    finds: list[EventFind]


class ClaimResult(BaseModel):
    """Итог засчёта находки-элемента интерфейса."""

    find: EventFind
    found: int = Field(ge=0)
    total: int = Field(ge=0)
    completed: bool
    newly_unlocked: bool = Field(description="false при повторе: награда не выдаётся второй раз")


class EventArchiveItem(BaseModel):
    """Запуск в архиве игрока."""

    key: EventKey
    title: str
    status: RunStatus
    starts_at: datetime
    ends_at: datetime
    found: int = Field(ge=0)
    total: int = Field(ge=0)
    completed: bool


class MyEvents(BaseModel):
    """Архив событий игрока (новые первыми, не более 50)."""

    items: list[EventArchiveItem] = Field(max_length=50)
