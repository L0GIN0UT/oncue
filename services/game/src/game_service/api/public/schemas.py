"""Схемы публичного API game (план этапа 2, §2.6) — контракт для фронтенда и bff.

Античит (D11): до ревила в снимке сессии нет ``media_item_id``, названия и
``asset_set_id``; ``reveal`` есть только у завершённого раунда.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Domain = Literal["music", "film"]
ModeKey = Annotated[
    str, StringConstraints(pattern=r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=100)
]
PresetId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
RoundOutcomeLiteral = Literal["exact", "partial", "miss", "gave_up", "void"]
GuessResultLiteral = Literal["exact", "partial", "miss"]
ClueKind = Literal["audio", "image"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------- режимы
class LevelOut(BaseModel):
    """Уровень загадки для UI."""

    index: int = Field(ge=1)
    hint: dict[str, float | int | str] = Field(
        description="Что открывает уровень: {seconds} для аудио, {blur_px, crop} для кадра"
    )


class PresetOut(BaseModel):
    """Пресет случайного режима."""

    id: str
    title_key: str


class ModeOut(BaseModel):
    """Опубликованный режим (``GET /v1/modes``)."""

    key: str
    version: int
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int
    levels: list[LevelOut]
    allow_guest: bool
    presets: list[PresetOut] | None = None


# ----------------------------------------------------------------------------- сессия
class StartSessionRequest(_Request):
    """Старт сессии режима."""

    mode_key: ModeKey
    preset_id: PresetId | None = None


class ModeRef(BaseModel):
    """Режим сессии (версия фиксируется при старте)."""

    key: str
    version: int
    domain: Domain
    clue_type: str


class GuessOut(BaseModel):
    """Ход в истории раунда."""

    kind: Literal["item", "text", "skip"]
    display: str
    result: GuessResultLiteral | None = Field(description="null у пропуска")


class PersonOut(BaseModel):
    """Персона в ревиле."""

    name: str
    role: str


class FinalClue(BaseModel):
    """Полный ассет для ревила."""

    kind: ClueKind
    url: str
    expires_at: datetime
    mime: str


class Reveal(BaseModel):
    """Ответ раунда — только у завершённого раунда."""

    media_item_id: UUID
    title: str
    year: int | None
    persons: list[PersonOut]
    final_clue: FinalClue


class RoundOut(BaseModel):
    """Раунд в снимке сессии."""

    index: int = Field(ge=0)
    status: Literal["pending", "active", "finished", "void"]
    level: int = Field(ge=1, description="Текущий открытый уровень")
    levels_count: int = Field(ge=1)
    guesses: list[GuessOut]
    points: int | None = None
    reveal: Reveal | None = Field(default=None, description="Только у завершённого раунда")


class SessionSnapshot(BaseModel):
    """Снимок сессии; ``version`` — для ``expected_version`` следующего хода."""

    id: UUID
    version: int
    status: Literal["active", "finished", "abandoned"]
    mode: ModeRef
    day: date | None = None
    score: int
    max_score: int
    current_round: int
    rounds: list[RoundOut]


# ----------------------------------------------------------------------------- ассеты
class ClueRequest(_Request):
    """Запрос ассета уровня (уровень не выше открытого)."""

    level: int = Field(ge=1, le=20)


class ClueMeta(BaseModel):
    """Параметры уровня для UI."""

    seconds: float | None = None
    blur: float | None = None


class ClueOut(BaseModel):
    """Короткоживущая ссылка на ассет уровня (TTL 60 с)."""

    level: int
    kind: ClueKind
    url: str
    expires_at: datetime
    mime: str
    meta: ClueMeta


# ----------------------------------------------------------------------------- ходы
class ItemAnswer(_Request):
    """Ответ выбором из автокомплита."""

    media_item_id: UUID


class TextAnswer(_Request):
    """Ответ свободным текстом."""

    text: str = Field(min_length=1, max_length=120)


class GuessRequest(_Request):
    """Ответ на текущем уровне раунда."""

    round_index: int = Field(ge=0, le=49)
    expected_version: int = Field(ge=0)
    answer: ItemAnswer | TextAnswer


class SkipRequest(_Request):
    """Пропуск уровня."""

    round_index: int = Field(ge=0, le=49)
    expected_version: int = Field(ge=0)


class GiveUpRequest(_Request):
    """Сдаться в раунде."""

    expected_version: int = Field(ge=0)


class MoveResult(BaseModel):
    """Итог хода."""

    result: GuessResultLiteral | None = Field(
        description="Результат ответа; null для пропуска и сдачи"
    )
    round: RoundOut
    session: SessionSnapshot


# ----------------------------------------------------------------------------- итог
class ResultRound(BaseModel):
    """Раунд в итоге сессии."""

    index: int
    outcome: RoundOutcomeLiteral
    level: int | None
    points: int
    reveal: Reveal | None = Field(description="null у раунда void (ассет недоступен)")


class ShareOut(BaseModel):
    """Текст-сетка для «Поделиться» (D14)."""

    text: str


class DailyRoundStat(BaseModel):
    """Доля угадавших раунд дейлика (только пользователи)."""

    index: int
    exact_pct: float = Field(ge=0, le=100)


class SessionResult(BaseModel):
    """Итог завершённой сессии."""

    score: int
    max_score: int
    rounds: list[ResultRound]
    share: ShareOut
    daily_stats: list[DailyRoundStat] | None = None


class DailyStatus(BaseModel):
    """Статус дейлика для текущего субъекта (гость или пользователь)."""

    mode_key: str
    day: date
    status: Literal["not_started", "in_progress", "finished"]
    session_id: UUID | None = None
    score: int | None = None
    max_score: int
    next_reset_at: datetime
