"""Схемы публичного API bff_web (план этапа 2, §2.6; этап 3a, §2.5) — экраны веб-клиента.

bff только читает (D4): агрегирует game, identity и catalog. Блок упавшего сервиса
отдаётся как ``{"status": "unavailable"}`` — страница рендерится без него. Подачу (формат
ответа, вид итога, выбор перед стартом) фронтенд выбирает по данным (D12), не по ключу режима.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Domain = Literal["music", "film"]
AnswerFormat = Literal["classic", "pick"]
Presentation = Literal["score", "streak", "bracket"]


class Block[T](BaseModel):
    """Блок страницы: данные или признак недоступности источника."""

    status: Literal["ok", "unavailable"]
    data: T | None = None


# ----------------------------------------------------------------------------- субъект
class Subject(BaseModel):
    """Кто смотрит страницу."""

    kind: Literal["user", "guest"]
    display_name: str | None = None


class SessionInfo(BaseModel):
    """``GET /v1/session``: ``subject = null`` — нет токена или он недействителен."""

    subject: Subject | None


# ----------------------------------------------------------------------------- карточки
class PresetCard(BaseModel):
    """Пресет случайного режима."""

    id: str
    title_key: str


class SubjectPickCard(BaseModel):
    """Выбор персон перед стартом (марафон): ``GET game /v1/modes/{key}/subjects``."""

    roles: list[str]
    max: int = Field(ge=1)


class ModeStartCard(BaseModel):
    """Что игрок выбирает перед стартом режима."""

    subject: SubjectPickCard | None = None


class ModeCard(BaseModel):
    """Карточка режима (из ``game /v1/modes``)."""

    key: str
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int = Field(description="Число раундов; у марафона и пары — максимум")
    allow_guest: bool
    presets: list[PresetCard] | None = None
    answer_format: AnswerFormat = Field(default="classic", description="Реестр ответов фронта")
    presentation: Presentation = Field(default="score", description="Реестр итогов фронта")
    start: ModeStartCard | None = Field(default=None, description="null — выбирать нечего")


class ModeGroup(BaseModel):
    """Группа витрины: режимы с одинаковыми ``source_type`` и ``presentation``."""

    source_type: str
    presentation: Presentation
    modes: list[ModeCard]


class DailyCard(BaseModel):
    """Карточка планового режима периода + статус субъекта (``game /v1/modes`` + ``/v1/daily``).

    Дейлик — период «день», недельная сетка — «неделя»; ``day`` — начало периода,
    ``next_reset_at`` — его конец.
    """

    mode_key: str
    domain: Domain
    title_key: str
    day: date
    status: Literal["not_started", "in_progress", "finished"]
    session_id: UUID | None = None
    score: int | None = None
    max_score: int
    next_reset_at: datetime
    presentation: Presentation = "score"


class RecordCard(BaseModel):
    """Личный рекорд режима (``game /v1/me/records``)."""

    mode_key: str
    domain: Domain
    title_key: str
    presentation: Presentation
    best: int
    percentile: float | None = Field(default=None, ge=0, le=100)
    achieved_at: datetime


class HomeBlocks(BaseModel):
    """Блоки главной."""

    dailies: Block[list[DailyCard]] = Field(description="Наборы дня (без недельной сетки)")
    modes: Block[list[ModeCard]]
    weekly: Block[list[DailyCard]] = Field(description="Наборы недели (сетка)")


class HomePage(BaseModel):
    """``GET /v1/pages/home``."""

    blocks: HomeBlocks


class SoloBlocks(BaseModel):
    """Блоки хаба соло-режимов."""

    daily: Block[list[DailyCard]] = Field(description="Наборы дня")
    random: Block[list[ModeCard]] = Field(description="Случайные режимы (витрина этапа 2)")
    modes: Block[list[ModeGroup]] = Field(
        description="Витрина: неплановые режимы домена по группам (source_type, presentation)"
    )
    weekly: Block[list[DailyCard]] = Field(description="Наборы недели (сетка)")
    records: Block[list[RecordCard]] | None = Field(
        default=None, description="Личные рекорды; null — гость или аноним"
    )


class SoloPage(BaseModel):
    """``GET /v1/pages/solo``."""

    blocks: SoloBlocks


# ----------------------------------------------------------------------------- итог
class PersonCard(BaseModel):
    """Персона в карточке единицы."""

    name: str
    role: str


class ExternalLink(BaseModel):
    """Ссылка на внешний сервис (TMDB, MusicBrainz …)."""

    kind: str
    url: str


class ItemCard(BaseModel):
    """Карточка единицы из ``catalog /v1/items/{id}``."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    genres: list[str]
    persons: list[PersonCard]
    external_links: list[ExternalLink] = Field(default_factory=list)


class ResultRoundCard(BaseModel):
    """Раунд итога (из ``game /v1/sessions/{id}/result``)."""

    index: int
    outcome: Literal["exact", "partial", "miss", "gave_up", "void"]
    level: int | None
    points: int
    media_item_id: UUID | None = Field(description="null у раунда void")
    exact_pct: float | None = Field(
        default=None, ge=0, le=100, description="Доля угадавших (только дейлик)"
    )


class ResultRecord(BaseModel):
    """Рекорд в итоге (только пользователи, режимы с рекордами)."""

    best: int
    is_new: bool
    percentile: float | None = Field(default=None, ge=0, le=100)


class BracketPick(BaseModel):
    """Выбор в сетке: тур и названия победителя и проигравшего."""

    stage: int
    winner: str
    loser: str


class BracketSummary(BaseModel):
    """Итог сетки: чемпион и выборы по турам."""

    champion: str
    picks: list[BracketPick]


class ResultSummary(BaseModel):
    """Итог сессии для экрана результатов."""

    session_id: UUID
    mode_key: str | None = None
    score: int
    max_score: int
    share_text: str
    rounds: list[ResultRoundCard]
    presentation: Presentation = Field(default="score", description="Реестр итогов фронта")
    record: ResultRecord | None = Field(default=None, description="null — гость или без рекордов")
    bracket: BracketSummary | None = Field(default=None, description="Только у сетки")


class ResultsBlocks(BaseModel):
    """Блоки экрана результатов."""

    result: Block[ResultSummary]
    items: Block[list[ItemCard]]


class ResultsPage(BaseModel):
    """``GET /v1/pages/results/{session_id}``."""

    blocks: ResultsBlocks
