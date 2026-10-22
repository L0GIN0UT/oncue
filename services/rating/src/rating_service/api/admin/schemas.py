"""Схемы admin API rating (ADR 0037, 0039).

Конфиг ladder-а — тело по ``contracts/rating/ladder.v1.json``; полную проверку делает домен
rating (потока G), здесь он объект: схема JSON Schema — отдельный контракт, а не дубль
pydantic-моделью.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

LadderKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Reason = Annotated[str, StringConstraints(min_length=3, max_length=500)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LadderVersionCard(BaseModel):
    """Версия конфига ladder-а."""

    version: int = Field(ge=1)
    status: Literal["draft", "published", "archived"]
    created_at: datetime
    published_at: datetime | None = None
    created_by: UUID


class AdminLadderCard(BaseModel):
    """Ladder в админке: действующая и черновая версии."""

    key: LadderKey
    published_version: int | None = Field(description="null — ещё не публиковался")
    draft_version: int | None
    season_id: UUID | None = Field(description="Текущий сезон; null — между сезонами")


class AdminLadderList(BaseModel):
    """Ladder-ы админки."""

    items: list[AdminLadderCard]


class LadderVersionList(BaseModel):
    """Версии ladder-а, новые первыми."""

    items: list[LadderVersionCard]


class LadderVersionBody(BaseModel):
    """Версия ladder-а с телом конфига."""

    card: LadderVersionCard
    config: dict[str, JsonValue] = Field(description="По `contracts/rating/ladder.v1.json`")


class SaveDraftRequest(_Request):
    """Сохранить черновик конфига ladder-а."""

    config: dict[str, JsonValue] = Field(
        description="По `contracts/rating/ladder.v1.json`; ключ в теле равен ключу в пути"
    )
    reason: Reason


class PublishRequest(_Request):
    """Опубликовать версию (черновик становится действующим, уходит `LadderPublished`)."""

    version: int = Field(ge=1)
    reason: Reason


class RollbackRequest(_Request):
    """Откатить ladder к ранее опубликованной версии (публикуется новой версией-копией)."""

    to_version: int = Field(ge=1)
    reason: Reason


class SeasonCard(BaseModel):
    """Сезон ladder-а."""

    season_id: UUID
    ladder: LadderKey
    number: int = Field(ge=1)
    status: Literal["scheduled", "active", "finished"]
    starts_at: datetime
    ends_at: datetime
    finished_at: datetime | None = None


class CreateSeasonRequest(_Request):
    """Создать сезон: по умолчанию — следующий сразу после текущего, длина — из конфига."""

    starts_at: datetime | None = Field(default=None, description="null — после текущего сезона")
    reason: Reason


class CloseSeasonRequest(_Request):
    """Закрыть сезон досрочно: мягкий сброс, `SeasonFinished`, следующий сезон открывается."""

    reason: Reason


class RebuildRequest(_Request):
    """Пересобрать лидерборд сезона из Postgres."""

    reason: Reason


class RebuildAccepted(BaseModel):
    """Пересборка принята (выполняет задача ``rating.leaderboards.rebuild``)."""

    ladder: LadderKey
    season_id: UUID
    status: Literal["accepted"] = "accepted"
