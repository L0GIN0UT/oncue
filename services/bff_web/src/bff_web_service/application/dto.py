"""Данные источников (game, catalog, identity, collections, library) и экраны из них.

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
Difficulty = Literal["easy", "medium", "hard"]
CollectionSort = Literal["featured", "popular", "new", "likes", "difficulty"]
RoundsChoice = Literal[5, 10, 25, "all"]


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


# ----------------------------------------------------------------------------- collections
@dataclass(frozen=True, slots=True)
class CollectionAuthor:
    """Автор сборника: пользователь, куратор («Guessverse») или удалённый."""

    kind: Literal["user", "curator", "deleted"]
    user_id: UUID | None = None
    nickname: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionCover:
    """Обложка-пресет (градиент и эмодзи — ``collections /v1/meta``)."""

    preset: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CollectionCard:
    """Карточка сборника в каталоге (``collections GET /v1/collections``), без состава."""

    collection_id: UUID
    domain: Domain
    kind: Literal["ugc", "curated"]
    title: str
    cover: CollectionCover | None = None
    tags: list[str]
    author: CollectionAuthor
    default_mode_key: str
    items_count: int
    likes_count: int
    plays_count: int
    difficulty: Difficulty | None = None
    featured: bool = False
    published_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionList:
    """Страница каталога сборников по курсору (курсор привязан к сортировке)."""

    items: list[CollectionCard]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CustomClue:
    """Своя загадка единицы (эмодзи); видят только автор и админ."""

    emoji: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionItem:
    """Единица состава сборника — только если состав открыт субъекту (D6)."""

    position: int
    media_item_id: UUID
    title: str
    available: bool
    year: int | None = None
    custom: CustomClue | None = None


@dataclass(frozen=True, slots=True)
class CollectionMe:
    """Отношение пользователя к сборнику (у гостя и анонима его нет)."""

    liked: bool
    favorite: bool
    completed: bool
    is_author: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CollectionDetail(CollectionCard):
    """Сборник целиком (``collections GET /v1/collections/{id}``)."""

    description: str | None = None
    visibility: Literal["public", "unlisted"]
    status: Literal["published", "hidden"]
    version: int
    items_visible: bool
    items: list[CollectionItem] | None = None
    me: CollectionMe | None = None


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """Параметры каталога сборников (``collections GET /v1/collections``)."""

    q: str | None = None
    domain: Domain | None = None
    mode: str | None = None
    difficulty: Difficulty | None = None
    curated: bool | None = None
    sort: CollectionSort = "popular"
    cursor: str | None = None
    limit: int = 20


@dataclass(frozen=True, slots=True)
class CollectionMode:
    """Режим сборника и его играбельность (``game GET /v1/collections/{id}/modes``)."""

    mode_key: str
    title_key: str
    clue_type: str
    allow_guest: bool
    playable: int
    total: int
    available: bool
    round_options: list[RoundsChoice]


# ----------------------------------------------------------------------------- library
@dataclass(frozen=True, slots=True, kw_only=True)
class Shelf:
    """Полка медиатеки (``library GET /v1/shelves``)."""

    ref: str
    shelf_id: UUID | None = None
    kind: Literal["system", "custom"]
    key: str | None = None
    title: str | None = None
    title_key: str | None = None
    domains: list[Domain]
    exclusive_with: list[str] = field(default_factory=list)
    items_count: int
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ShelfItem:
    """Единица на полке (карточка из проекции catalog в library)."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    year: int | None = None
    persons: list[str] = field(default_factory=list)
    status: Literal["available", "unavailable"]
    source: Literal["reveal", "results", "card", "collection", "library"]
    added_at: datetime


@dataclass(frozen=True, slots=True)
class ShelfItems:
    """Полка и страница её единиц (``library GET /v1/shelves/{ref}/items``)."""

    shelf: Shelf
    items: list[ShelfItem]
    next_cursor: str | None = None


# ----------------------------------------------------------------------------- экраны 3b
@dataclass(frozen=True, slots=True)
class CollectionsBlocks:
    """Блоки каталога: без ``q`` и курсора — витрины, иначе только ``results``.

    Блок, которого нет в этом виде страницы, — ``None``.
    """

    featured: Block[CollectionList] | None = None
    popular: Block[CollectionList] | None = None
    new: Block[CollectionList] | None = None
    results: Block[CollectionList] | None = None


@dataclass(frozen=True, slots=True)
class CollectionsPage:
    """``GET /v1/pages/collections``.

    Attributes:
        cacheable: Ответ можно отдавать общим кэшам (``public``): каталог без ``q``.
    """

    blocks: CollectionsBlocks
    cacheable: bool


@dataclass(frozen=True, slots=True)
class CollectionBlocks:
    """Блоки страницы сборника."""

    collection: Block[CollectionDetail]
    modes: Block[list[CollectionMode]]


@dataclass(frozen=True, slots=True)
class CollectionPage:
    """``GET /v1/pages/collections/{id}``: ``me`` — только пользователю."""

    blocks: CollectionBlocks
    personal: bool
    me: CollectionMe | None = None


@dataclass(frozen=True, slots=True)
class LibraryBlocks:
    """Блоки медиатеки."""

    shelves: Block[list[Shelf]]


@dataclass(frozen=True, slots=True)
class LibraryPage:
    """``GET /v1/pages/library``."""

    blocks: LibraryBlocks


@dataclass(frozen=True, slots=True)
class LibraryShelfBlocks:
    """Блоки страницы полки."""

    shelf: Block[ShelfItems]


@dataclass(frozen=True, slots=True)
class LibraryShelfPage:
    """``GET /v1/pages/library/{ref}``."""

    blocks: LibraryShelfBlocks


# ----------------------------------------------------------------------------- match (этап 4a)
MatchKind = Literal["duel", "party", "lobby"]
MatchFinish = Literal["completed", "forfeit", "tech_win", "aborted"]
PlayerFinish = Literal["completed", "forfeit", "left", "tech_win", "tech_loss"]
DuelOutcome = Literal["win", "loss", "draw"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchMode:
    """Шаблон режима комнаты или матча (``match ModeRef``): версия фиксируется при создании."""

    key: str
    version: int
    domain: Domain
    kind: MatchKind
    title_key: str
    clue_type: str
    answer_format: str
    ranked: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class RoomSettings:
    """Действующие настройки комнаты (шаблон + переопределения хоста)."""

    rounds: int
    level_window_s: int
    answer_format: str
    clue_type: str
    players_max: int
    visibility: Literal["public", "private"]
    collection_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenRoom:
    """Открытое лобби (``match GET /v1/rooms/open``)."""

    code: str
    mode: MatchMode
    host_nickname: str
    players: int
    players_max: int
    settings: RoomSettings
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OpenRooms:
    """Страница открытых лобби (курсор — для «показать все» напрямую в match)."""

    items: list[OpenRoom]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MyMatch:
    """Матч в «Моих матчах» (``match GET /v1/me/matches``)."""

    match_id: UUID
    mode: MatchMode
    finished_at: datetime
    finish: MatchFinish
    players: int
    place: int
    score: int
    outcome: DuelOutcome | None
    my_finish: PlayerFinish


@dataclass(frozen=True, slots=True)
class MyMatches:
    """Страница моих матчей."""

    items: list[MyMatch]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchPlayer:
    """Игрок в итоге матча: место с ничьими, ``outcome`` — только у дуэли."""

    user_id: UUID
    nickname: str
    slot: int
    place: int
    score: int
    outcome: DuelOutcome | None
    finish: PlayerFinish


@dataclass(frozen=True, slots=True, kw_only=True)
class RoundPlayerResult:
    """Результат игрока в раунде (``level = None`` — не угадал)."""

    user_id: UUID
    level: int | None
    partial: bool
    score: int
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchRound:
    """Раунд итога: единица раскрыта после матча, карточку берём из catalog."""

    index: int
    media_item_id: UUID
    results: list[RoundPlayerResult]
    void: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class MatchResult:
    """Итог матча (``match GET /v1/matches/{id}``)."""

    match_id: UUID
    room_code: str
    mode: MatchMode
    started_at: datetime
    finished_at: datetime
    finish: MatchFinish
    players: list[MatchPlayer]
    rounds: list[MatchRound]
    collection_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ItemShelves:
    """На каких полках пользователя лежит единица (``library GET /v1/items/{id}/shelves``)."""

    media_item_id: UUID
    shelves: list[str]


# ----------------------------------------------------------------------------- экраны 4a
class OnlineStatus(StrEnum):
    """Состояние хаба онлайна: ``auth_required`` — гость или аноним (D13), блоков нет."""

    OK = "ok"
    AUTH_REQUIRED = "auth_required"


@dataclass(frozen=True, slots=True)
class OnlineBlocks:
    """Блоки хаба онлайна; каждый гаснет независимо (ADR 0017)."""

    open_rooms: Block[OpenRooms]
    my_matches: Block[MyMatches]
    modes: Block[list[ModeCard]]


@dataclass(frozen=True, slots=True)
class OnlinePage:
    """``GET /v1/pages/online``: гостю и анониму — ``auth_required`` без блоков."""

    status: OnlineStatus
    blocks: OnlineBlocks | None = None


@dataclass(frozen=True, slots=True)
class MatchBlocks:
    """Блоки экрана итога матча."""

    match: Block[MatchResult]
    items: Block[list[ItemCard]]
    library: Block[list[ItemShelves]]


@dataclass(frozen=True, slots=True)
class MatchPage:
    """``GET /v1/pages/match/{id}``."""

    blocks: MatchBlocks
