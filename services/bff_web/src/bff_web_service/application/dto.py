"""Данные источников (game, catalog, identity) и экраны, которые из них собираются.

Входные DTO повторяют поля контрактов ``contracts/openapi/<service>.json``, нужные экранам;
лишние поля источника отбрасываются при разборе (инфраструктура). Выходные — схемы §2.6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from bff_web_service.domain import Block

Domain = Literal["music", "film"]
DailyState = Literal["not_started", "in_progress", "finished"]
RoundOutcome = Literal["exact", "partial", "miss", "gave_up", "void"]
AnswerFormat = Literal["classic", "pick"]
Presentation = Literal["score", "streak", "bracket"]


# ----------------------------------------------------------------------------- зритель
class SubjectKind(StrEnum):
    """Вид субъекта токена."""

    USER = "user"
    GUEST = "guest"


@dataclass(frozen=True, slots=True)
class Viewer:
    """Кто смотрит страницу.

    Attributes:
        token: Access-токен, который уйдёт сервисам как Bearer; ``None`` — аноним.
        kind: Вид субъекта по проверенному токену; ``None`` — аноним или токен не удалось
            проверить локально (ключи identity недоступны).
        verified: Токен проверен локально (подпись, срок, отзыв).
    """

    token: str | None = None
    kind: SubjectKind | None = None
    verified: bool = False

    @property
    def is_personal(self) -> bool:
        """Ответ зависит от субъекта: не кэшировать ни в Redis, ни в браузере/CDN."""
        return self.token is not None


ANONYMOUS = Viewer()


# ----------------------------------------------------------------------------- game
@dataclass(frozen=True, slots=True)
class Preset:
    """Пресет случайного режима."""

    id: str
    title_key: str


@dataclass(frozen=True, slots=True)
class SubjectPick:
    """Выбор персон перед стартом (марафон): допустимые роли и сколько можно выбрать."""

    roles: list[str]
    max: int


@dataclass(frozen=True, slots=True)
class ModeStart:
    """Что игрок выбирает перед стартом режима (``None`` у полей — выбирать нечего)."""

    subject: SubjectPick | None = None


@dataclass(frozen=True, slots=True)
class Mode:
    """Опубликованный режим (``game GET /v1/modes``).

    Attributes:
        answer_format: Формат ответа (плагин ``AnswerFormat``): поле ввода или выбор варианта.
        presentation: Как показывать итог (объявляет Scorer): очки, серия, сетка.
        start: Выбор перед стартом; ``None`` — стартовать сразу (или по пресету).
    """

    key: str
    version: int
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int
    allow_guest: bool
    presets: list[Preset] | None = None
    answer_format: AnswerFormat = "classic"
    presentation: Presentation = "score"
    start: ModeStart | None = None


@dataclass(frozen=True, slots=True)
class DailyStatus:
    """Дейлик для текущего субъекта (``game GET /v1/daily``)."""

    mode_key: str
    day: date
    status: DailyState
    max_score: int
    next_reset_at: datetime
    session_id: UUID | None = None
    score: int | None = None


@dataclass(frozen=True, slots=True)
class Reveal:
    """Ответ раунда (нужен только id единицы для карточки catalog)."""

    media_item_id: UUID


@dataclass(frozen=True, slots=True)
class ResultRound:
    """Раунд итога."""

    index: int
    outcome: RoundOutcome
    level: int | None
    points: int
    reveal: Reveal | None


@dataclass(frozen=True, slots=True)
class DailyRoundStat:
    """Доля угадавших раунд дейлика."""

    index: int
    exact_pct: float


@dataclass(frozen=True, slots=True)
class Share:
    """Текст-сетка «Поделиться»."""

    text: str


@dataclass(frozen=True, slots=True)
class ResultRecord:
    """Рекорд в итоге сессии (только пользователи, режимы с ``features.records``)."""

    best: int
    is_new: bool
    percentile: float | None = None


@dataclass(frozen=True, slots=True)
class BracketPick:
    """Выбор в сетке: тур и названия победителя и проигравшего."""

    stage: int
    winner: str
    loser: str


@dataclass(frozen=True, slots=True)
class Bracket:
    """Итог сетки: чемпион и выборы по турам."""

    champion: str
    picks: list[BracketPick]


@dataclass(frozen=True, slots=True)
class SessionResult:
    """Итог завершённой сессии (``game GET /v1/sessions/{id}/result``)."""

    score: int
    max_score: int
    rounds: list[ResultRound]
    share: Share
    daily_stats: list[DailyRoundStat] | None = None
    presentation: Presentation = "score"
    record: ResultRecord | None = None
    bracket: Bracket | None = None


@dataclass(frozen=True, slots=True)
class PersonalRecord:
    """Личный рекорд режима (``game GET /v1/me/records``; гостю — пустой список)."""

    mode_key: str
    best: int
    achieved_at: datetime
    percentile: float | None = None


# ----------------------------------------------------------------------------- catalog
@dataclass(frozen=True, slots=True)
class Person:
    """Персона в карточке единицы."""

    name: str
    role: str


@dataclass(frozen=True, slots=True)
class Item:
    """Публичная карточка единицы (``catalog GET /v1/items/{id}``)."""

    id: UUID
    domain: Domain
    kind: str
    title: str
    original_title: str | None
    year: int | None
    genres: list[str]
    persons: list[Person]


# ----------------------------------------------------------------------------- identity
@dataclass(frozen=True, slots=True)
class Me:
    """Профиль пользователя (``identity GET /v1/me``) — только нужное экрану."""

    id: UUID
    display_name: str


# ----------------------------------------------------------------------------- экраны
@dataclass(frozen=True, slots=True)
class SubjectView:
    """Субъект для шапки сайта."""

    kind: SubjectKind
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class SessionView:
    """``GET /v1/session``."""

    subject: SubjectView | None


@dataclass(frozen=True, slots=True)
class ModeCard:
    """Карточка режима."""

    key: str
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int
    allow_guest: bool
    presets: list[Preset] | None = None
    answer_format: AnswerFormat = "classic"
    presentation: Presentation = "score"
    start: ModeStart | None = None


@dataclass(frozen=True, slots=True)
class ModeGroup:
    """Группа витрины: режимы с одинаковыми ``source_type`` и ``presentation``."""

    source_type: str
    presentation: Presentation
    modes: list[ModeCard]


@dataclass(frozen=True, slots=True)
class DailyCard:
    """Карточка планового режима периода (дейлик — день, сетка — неделя) + статус субъекта.

    ``day`` — начало периода, ``next_reset_at`` — его конец.
    """

    mode_key: str
    domain: Domain
    title_key: str
    day: date
    status: DailyState
    max_score: int
    next_reset_at: datetime
    session_id: UUID | None = None
    score: int | None = None
    presentation: Presentation = "score"


@dataclass(frozen=True, slots=True)
class RecordCard:
    """Личный рекорд режима для витрины."""

    mode_key: str
    domain: Domain
    title_key: str
    presentation: Presentation
    best: int
    achieved_at: datetime
    percentile: float | None = None


@dataclass(frozen=True, slots=True)
class HomeBlocks:
    """Блоки главной: ``dailies`` — наборы дня, ``weekly`` — наборы недели (сетка)."""

    dailies: Block[list[DailyCard]]
    modes: Block[list[ModeCard]]
    weekly: Block[list[DailyCard]]


@dataclass(frozen=True, slots=True)
class HomePage:
    """``GET /v1/pages/home``."""

    blocks: HomeBlocks
    personal: bool


@dataclass(frozen=True, slots=True)
class SoloBlocks:
    """Блоки хаба соло-режимов.

    Attributes:
        daily: Плановые режимы дня.
        random: Режимы ``random_source_types`` (витрина этапа 2, оставлена для совместимости).
        modes: Витрина: все неплановые режимы, сгруппированные по данным.
        weekly: Плановые режимы недели (сетка).
        records: Личные рекорды; ``None`` — гость или аноним (рекордов у них нет, D11).
    """

    daily: Block[list[DailyCard]]
    random: Block[list[ModeCard]]
    modes: Block[list[ModeGroup]]
    weekly: Block[list[DailyCard]]
    records: Block[list[RecordCard]] | None = None


@dataclass(frozen=True, slots=True)
class SoloPage:
    """``GET /v1/pages/solo``."""

    blocks: SoloBlocks
    personal: bool


@dataclass(frozen=True, slots=True)
class ResultRoundCard:
    """Раунд экрана результатов."""

    index: int
    outcome: RoundOutcome
    level: int | None
    points: int
    media_item_id: UUID | None
    exact_pct: float | None = None


@dataclass(frozen=True, slots=True)
class ResultSummary:
    """Итог сессии."""

    session_id: UUID
    score: int
    max_score: int
    share_text: str
    rounds: list[ResultRoundCard]
    mode_key: str | None = None
    presentation: Presentation = "score"
    record: ResultRecord | None = None
    bracket: Bracket | None = None


@dataclass(frozen=True, slots=True)
class ExternalLink:
    """Ссылка на внешний сервис (пока catalog их не отдаёт)."""

    kind: str
    url: str


@dataclass(frozen=True, slots=True)
class ItemCard:
    """Карточка единицы на экране результатов."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    original_title: str | None
    year: int | None
    genres: list[str]
    persons: list[Person]
    external_links: list[ExternalLink] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResultsBlocks:
    """Блоки экрана результатов."""

    result: Block[ResultSummary]
    items: Block[list[ItemCard]]


@dataclass(frozen=True, slots=True)
class ResultsPage:
    """``GET /v1/pages/results/{session_id}``."""

    blocks: ResultsBlocks
