"""Схемы internal-API progression (без персональных данных: только id и числа уровня)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class PlayerLevel(BaseModel):
    """Уровень игрока."""

    user_id: UUID
    level: int = Field(ge=1)
    xp_in_level: int = Field(ge=0, description="Опыт внутри текущего уровня")


class PlayerLevels(BaseModel):
    """Уровни запрошенных игроков (порядок запроса, дубли схлопнуты).

    Игрока со скрытым профилем в ответе нет; игрок, ещё ничего не получавший, — 1 уровень.
    """

    items: list[PlayerLevel]
