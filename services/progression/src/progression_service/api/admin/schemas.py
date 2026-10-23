"""Схемы admin API progression (ADR 0042–0044).

Конфиг — тело по ``contracts/progression/progression_config.v1.json``; полную проверку
(монотонные пороги, непрерывные полосы мест, метрики из реестра) делает домен progression
при сохранении черновика, здесь он объект: JSON Schema — отдельный контракт.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from gv_service import Reason
from progression_service.api.public.schemas import Bucket, Key, QuestPeriod, Reward

Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigVersionCard(BaseModel):
    """Версия конфига progression."""

    version: int = Field(ge=1)
    status: Literal["draft", "published", "archived"]
    created_at: datetime
    published_at: datetime | None = None
    created_by: UUID


class ConfigVersionList(BaseModel):
    """Версии конфига, новые первыми."""

    items: list[ConfigVersionCard]


class ConfigVersionBody(BaseModel):
    """Версия конфига с телом."""

    card: ConfigVersionCard
    config: dict[str, JsonValue] = Field(
        description="По `contracts/progression/progression_config.v1.json`"
    )


class SaveDraftRequest(_Request):
    """Сохранить черновик конфига (заменяет текущий черновик)."""

    config: dict[str, JsonValue]
    reason: Reason


class PublishRequest(_Request):
    """Опубликовать черновик: новая версия действует со следующего периода заданий."""

    version: int = Field(ge=1, description="Версия черновика (защита от гонки правок)")
    reason: Reason


class RollbackRequest(_Request):
    """Откат: копия старой версии становится новым черновиком."""

    reason: Reason


class GrantRequest(_Request):
    """Ручная выдача Версов."""

    amount: int = Field(ge=1, description="Версы; верхний предел — `admin.grant_max_amount`")
    bucket: Bucket = "earned"
    reason: Reason


class ReverseRequest(_Request):
    """Сторно проводки."""

    reason: Reason


class AdminLedgerEntry(BaseModel):
    """Проводка в админке."""

    op_id: str
    direction: Literal["credit", "debit"]
    amount: int = Field(ge=1)
    bucket: Bucket
    reason: Key
    source_ref: str
    reversal_of: str | None = None
    actor_id: UUID | None = Field(description="Админ; для автоматических — null")
    config_version: int = Field(ge=1)
    at: datetime


class AdminLedgerPage(BaseModel):
    """Страница журнала игрока, новые первыми."""

    user_id: UUID
    earned: int = Field(ge=0)
    paid: int = Field(ge=0)
    items: list[AdminLedgerEntry]
    next_cursor: Cursor | None = None


class LedgerOperation(BaseModel):
    """Итог ручной операции: проводка и баланс после неё."""

    entry: AdminLedgerEntry
    balance_after: int = Field(ge=0)


class AdminQuestCard(BaseModel):
    """Задание действующего конфига со статистикой."""

    key: Key
    period: QuestPeriod
    title: str
    reward: Reward
    enabled: bool
    completed_total: int = Field(ge=0)


class AdminQuestList(BaseModel):
    """Задания действующего конфига."""

    config_version: int = Field(ge=1)
    items: list[AdminQuestCard]


class AdminBadgeCard(BaseModel):
    """Бейдж действующего конфига со статистикой."""

    key: Key
    title: str
    enabled: bool
    unlocked_total: int = Field(ge=0)


class AdminBadgeList(BaseModel):
    """Бейджи действующего конфига."""

    config_version: int = Field(ge=1)
    items: list[AdminBadgeCard]
