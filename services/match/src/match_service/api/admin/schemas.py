"""Схемы admin API match (план этапа 4, §2.5, §10): активные комнаты, принудительное завершение."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from gv_service import Reason
from match_service.api.public.schemas import (
    MAX_PLAYERS,
    ModeRef,
    Phase,
    RoomCode,
    Visibility,
)


class AdminRoomCard(BaseModel):
    """Активная комната для раздела «Матчи» админки."""

    code: RoomCode
    mode: ModeRef
    visibility: Visibility
    phase: Phase
    host_id: UUID
    players: int = Field(ge=0, le=MAX_PLAYERS)
    match_id: UUID | None = None
    created_at: datetime
    owner_epoch: int | None = Field(
        default=None, ge=0, description="Эпоха lease владельца (D7); null — комната без владельца"
    )


class AdminRoomsPage(BaseModel):
    """Страница активных комнат (keyset-курсор)."""

    items: list[AdminRoomCard]
    next_cursor: str | None = None


class AbortRoomRequest(BaseModel):
    """Принудительное завершение комнаты: матч — ``finish = aborted``, причина в аудит."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class ResetPlayerRoomRequest(BaseModel):
    """Сброс указателя «активная комната» игрока (служебная команда); причина — в аудит."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class ResetPlayerRoomResult(BaseModel):
    """Что освобождено: код комнаты, на которую указывал указатель (`null` - указателя не было)."""

    released_code: RoomCode | None = None
