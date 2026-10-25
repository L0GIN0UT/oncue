"""Схемы admin API 5d: сезонные события (ADR 0054-0056, ``docs/plans/stage-5d-api.md``).

``EventDefinition`` повторяет ``contracts/progression/event_definition.v1.json`` (те же поля и
границы); соответствие проверяет ``tests/contract/test_event_definition.py``. Бизнес-правила
(одна финальная находка, уникальность ключей и номеров) - в домене потока E1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from gv_service import Reason
from progression_service.api.public.events_schemas import EventKey, RunStatus, ThemeKey

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
SlotKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]
ItemKey = Annotated[str, StringConstraints(max_length=64)]
TagKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
"""Ключ тега сборника - как `TagKey` в collections (ADR 0067)."""
Metric = Literal[
    "rounds_played",
    "correct_answers",
    "wins",
    "online_matches",
    "daily_completed",
    "collection_published",
    "daily_streak",
    "duel_win",
    "weekly_top_place",
    "wheel_spin_streak",
    "item_equipped",
    "collection_played",
]
PageKey = Literal[
    "home", "leaderboards", "profile", "collections", "match_summary", "shop", "wheel"
]
PlayPage = Literal[
    "home", "daily", "solo", "online", "collections", "leaderboards", "wheel", "shop", "profile"
]
RunState = Literal["draft", "published", "active", "finished", "stopped"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TriggerParams(_Strict):
    """Параметры метрики."""

    place_max: int | None = Field(default=None, ge=1, le=100)
    slot: SlotKey | None = None
    item_key: ItemKey | None = None
    domain: Literal["music", "film"] | None = None
    collection_keys: list[UUID] | None = Field(default=None, max_length=20)
    collection_tags: list[TagKey] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _unique_keys(self) -> TriggerParams:
        keys = self.collection_keys
        if keys is not None and len(set(keys)) != len(keys):
            raise ValueError("collection_keys: повторы")
        tags = self.collection_tags
        if tags is not None and len(set(tags)) != len(tags):
            raise ValueError("collection_tags: повторы")
        return self


class ServerTrigger(_Strict):
    """Находка открывается серверной метрикой."""

    type: Literal["server"]
    metric: Metric
    target: int = Field(ge=1, le=10_000)
    period: Literal["day", "event"]
    params: TriggerParams | None = None

    @model_validator(mode="after")
    def _collection_filters_only_collection_played(self) -> ServerTrigger:
        p = self.params
        has = p is not None and (p.collection_keys is not None or p.collection_tags is not None)
        if has and self.metric != "collection_played":
            raise ValueError("collection_keys/collection_tags - только у collection_played")
        return self


class UiSpotTrigger(_Strict):
    """Находка спрятана в элементе интерфейса страницы."""

    type: Literal["ui_spot"]
    page_key: PageKey


class FindRewardBody(_Strict):
    """Награда находки."""

    xp: int = Field(ge=0, le=5000)
    verses: int = Field(ge=0, le=1000)


class FindDefinition(_Strict):
    """Находка в определении."""

    key: Slug
    number: int = Field(ge=1, le=20)
    title: str = Field(min_length=1, max_length=120)
    diary_before: str = Field(max_length=1000)
    diary_after: str = Field(max_length=1000)
    x_pct: float = Field(ge=0, le=100)
    y_pct: float = Field(ge=0, le=100)
    reward: FindRewardBody
    final: bool
    play_page: PlayPage | None = None
    trigger: Annotated[ServerTrigger | UiSpotTrigger, Field(discriminator="type")]


class MainReward(_Strict):
    """Основная награда за закрытие всех находок."""

    item_key: ItemKey | None


class EventDefinition(_Strict):
    """Тело определения события (``event_definition.v1``)."""

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=2000)
    theme_key: ThemeKey
    finds: list[FindDefinition] = Field(min_length=2, max_length=20)
    reward: MainReward

    @model_validator(mode="after")
    def _single_final(self) -> EventDefinition:
        if sum(f.final for f in self.finds) != 1:
            raise ValueError("ровно одна находка должна быть финальной")
        return self


class CreateEventRequest(_Strict):
    """Создать событие. Повтор `key` - 409 `progression.event_key_taken`."""

    key: EventKey
    body: EventDefinition
    reason: Reason


class UpdateEventRequest(_Strict):
    """Заменить определение (есть запуск `published/active` - 409)."""

    body: EventDefinition
    expected_body_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "`body_sha256`, который видел админ; текущий хеш другой - 409 "
            "`progression.event_definition_changed`. Не задан - без проверки"
        ),
    )
    reason: Reason


class CreateRunRequest(_Strict):
    """Новый запуск (и перезапуск) события: черновик с периодом."""

    starts_at: AwareDatetime
    ends_at: AwareDatetime
    reason: Reason


class PublishRunRequest(_Strict):
    """Публикация запуска с проверкой, что определение не менялось."""

    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: Reason


class StopRunRequest(_Strict):
    """Остановка запуска."""

    reason: Reason


class AdminRun(BaseModel):
    """Запуск события; `active/finished` считается по времени."""

    id: UUID
    seq: int = Field(ge=1)
    status: RunState
    starts_at: datetime
    ends_at: datetime
    published_at: datetime | None
    stopped_at: datetime | None


class AdminEvent(BaseModel):
    """Событие в админке."""

    key: EventKey
    body: EventDefinition
    body_sha256: str
    map_url: str | None
    runs: list[AdminRun]
    updated_at: datetime


class AdminEventSummary(BaseModel):
    """Строка списка событий."""

    key: EventKey
    title: str
    status: RunState | RunStatus | None = Field(
        description="Статус последнего запуска; null - запусков нет"
    )
    latest_run: AdminRun | None


class AdminEventList(BaseModel):
    """Список событий."""

    items: list[AdminEventSummary]


class FindStat(BaseModel):
    """Сколько игроков открыли находку."""

    find_key: Slug
    number: int
    unlocked: int = Field(ge=0)


class RunStats(BaseModel):
    """Статистика запуска."""

    run_id: UUID
    players: int = Field(ge=0)
    completed: int = Field(ge=0)
    finds: list[FindStat]
