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
MatchKind = Literal["duel", "party", "lobby"]


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
        token_rejected: Токен был, но не прошёл локальную проверку (истёк, отозван,
            подделан): для экранов это аноним, а хаб онлайна просит фронт обновить сессию.
        user_id: Субъект проверенного токена (``sub``); ``None`` — аноним или токен не
            проверялся локально (JWKS недоступен): решения «это я» тогда не принимаются.
    """

    token: str | None = None
    kind: SubjectKind | None = None
    verified: bool = False
    token_rejected: bool = False
    user_id: UUID | None = None

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
class OnlineChat:
    """Эмоции и готовые фразы (ключи i18n) и признак свободного чата (только лобби)."""

    emotes: list[str]
    phrases: list[str]
    free_text: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class OnlineRules:
    """Правила комнаты онлайн-режима (``ModeOut.online``, ADR 0032).

    Attributes:
        lobby: Только у кастомного лобби: ``{visibility, overridable}`` — из чего выбирает
            хост; bff передаёт как есть (схема — ``$defs/LobbySpec`` конфига режима).
        max_collections: Только у лобби по сборнику: сколько сборников можно выбрать.
    """

    kind: MatchKind
    players_min: int
    players_max: int
    rounds: int
    level_windows_s: list[int]
    contest_type: str
    ranked: bool
    chat: OnlineChat
    ladder: str | None = None
    max_collections: int | None = None
    lobby: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class Mode:
    """Опубликованный режим (``game GET /v1/modes``).

    Attributes:
        answer_format: Формат ответа (плагин ``AnswerFormat``): поле ввода или выбор варианта.
        presentation: Как показывать итог (объявляет Scorer): очки, серия, сетка.
        start: Выбор перед стартом; ``None`` — стартовать сразу (или по пресету).
        online: Правила комнаты — только у онлайн-режима (``session=online``).
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
    online: OnlineRules | None = None


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
    progress: Block[HomeProgressCard] | None = None
    event: EventBannerCard | None = None


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
    comments: Block[CommentsCard] | None = None


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


@dataclass(frozen=True, slots=True)
class MatchParticipant:
    """Другой игрок матча в карточке истории (``can_report`` решает bff, как в итоге)."""

    user_id: UUID
    nickname: str
    can_report: bool


@dataclass(frozen=True, slots=True)
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
    participants: list[MatchParticipant] = field(default_factory=list)
    """ADR 0069: другие игроки матча для жалобы из истории; пусто - не удалось получить."""


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
    can_report: bool = False
    """5c: показывать кнопку жалобы; решает bff по данным матча, источник поле не отдаёт."""
    can_add_friend: bool = False
    """ADR 0069: можно звать в друзья (``can_send_request`` из social); сбой social - ``False``."""


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


# ----------------------------------------------------------------------------- рейтинг (4b)
QueueFormat = Literal["classic", "lite"]


@dataclass(frozen=True, slots=True)
class TierCard:
    """Звание ladder-а (ключи i18n из конфига rating)."""

    key: str
    family: str
    step: int | None
    title_key: str


@dataclass(frozen=True, slots=True)
class QueuePoolCard:
    """Сколько игроков ищут матч в очереди ladder-а и формата (``match /v1/queue/pool``)."""

    ladder: str
    format: QueueFormat
    searching: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RatingCard:
    """Рейтинг пользователя в ladder-е; в калибровке ``rating`` и ``tier`` — ``None``."""

    ladder: str
    rating: int | None
    tier: TierCard | None
    next_tier_at: int | None = None
    calibrating: bool
    calibration_left: int
    matches_played: int
    place: int | None = None


@dataclass(frozen=True, slots=True)
class SeasonCard:
    """Действующий сезон ladder-а."""

    ladder: str
    number: int
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class WeeklyEntryCard:
    """Строка топа недели."""

    place: int
    user_id: UUID
    nickname: str
    delta: int


@dataclass(frozen=True, slots=True)
class WeeklyTopCard:
    """Топ недели ladder-а (прирост с понедельника)."""

    ladder: str
    week_start: date
    items: list[WeeklyEntryCard]
    me: WeeklyEntryCard | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LadderCard:
    """Ladder для переключателя и хаба: формат, звания, действующий сезон."""

    key: str
    domain: Domain
    formats: list[QueueFormat]
    season: SeasonCard | None = None
    tiers: list[TierCard]


@dataclass(frozen=True, slots=True)
class LeaderboardEntryCard:
    """Строка лидерборда."""

    place: int
    user_id: UUID
    nickname: str
    rating: int
    tier: TierCard
    matches_played: int


@dataclass(frozen=True, slots=True)
class LeaderboardCard:
    """Страница лидерборда; ``next_cursor`` отдаёт rating."""

    ladder: str
    items: list[LeaderboardEntryCard]
    next_cursor: str | None = None
    me: LeaderboardEntryCard | None = None


@dataclass(frozen=True, slots=True)
class LeaderboardsBlocks:
    """Блоки страницы лидербордов; каждый гаснет независимо."""

    ladders: Block[list[LadderCard]]
    leaderboard: Block[LeaderboardCard]
    weekly: Block[WeeklyTopCard]


@dataclass(frozen=True, slots=True)
class LeaderboardsPage:
    """``GET /v1/pages/leaderboards``.

    Attributes:
        blocks: Блоки страницы.
        personal: В rating уходил токен пользователя (``me``): ответ не кэшируется.
    """

    blocks: LeaderboardsBlocks
    personal: bool = False


# ----------------------------------------------------------------------------- экраны 4a
class OnlineStatus(StrEnum):
    """Состояние хаба онлайна; кроме ``ok`` блоков нет.

    ``auth_required`` — гость или аноним (D13): фронт показывает экран регистрации.
    ``session_expired`` — токен не прошёл проверку или match ответил 401: фронт обновляет
    сессию (refresh) и повторяет запрос.
    """

    OK = "ok"
    AUTH_REQUIRED = "auth_required"
    SESSION_EXPIRED = "session_expired"


@dataclass(frozen=True, slots=True, kw_only=True)
class OnlineModeCard:
    """Онлайн-режим для создания комнаты: карточка + правила комнаты."""

    key: str
    version: int
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    answer_format: AnswerFormat
    presentation: Presentation
    online: OnlineRules


@dataclass(frozen=True, slots=True)
class OnlineBlocks:
    """Блоки хаба онлайна; каждый гаснет независимо (ADR 0017)."""

    open_rooms: Block[OpenRooms]
    my_matches: Block[MyMatches]
    modes: Block[list[OnlineModeCard]]
    queues: Block[list[QueuePoolCard]]
    ratings: Block[list[RatingCard]]
    seasons: Block[list[SeasonCard]]
    weekly: Block[list[WeeklyTopCard]]
    friends: Block[FriendsCard] | None = None


@dataclass(frozen=True, slots=True)
class OnlinePage:
    """``GET /v1/pages/online``: блоки есть только при ``status = ok``."""

    status: OnlineStatus
    blocks: OnlineBlocks | None = None


@dataclass(frozen=True, slots=True)
class MatchBlocks:
    """Блоки экрана итога матча."""

    match: Block[MatchResult]
    items: Block[list[ItemCard]]
    library: Block[list[ItemShelves]]
    appearance: Block[list[PlayerAppearanceCard]] | None = None


@dataclass(frozen=True, slots=True)
class MatchPage:
    """``GET /v1/pages/match/{id}``."""

    blocks: MatchBlocks


# ----------------------------------------------------------------------------- progression (5a)
PrizeKind = Literal["xp", "verses", "boost", "item", "none"]


@dataclass(frozen=True, slots=True)
class StreakCard:
    """Серия дейлика по домену."""

    domain: Domain
    current: int
    best: int


@dataclass(frozen=True, slots=True)
class BoostCard:
    """Действующий буст опыта."""

    multiplier: float
    matches_left: int


@dataclass(frozen=True, slots=True)
class ProgressCard:
    """Уровень и опыт (``progression GET /v1/me/progress``)."""

    level: int
    max_level: int
    total_xp: int
    xp_in_level: int
    xp_to_next: int | None
    solo_xp_today: int
    solo_xp_cap: int
    streaks: list[StreakCard]
    boost: BoostCard | None = None


@dataclass(frozen=True, slots=True)
class WalletCard:
    """Версы игрока (``GET /v1/me/wallet``)."""

    earned: int
    paid: int
    total: int


@dataclass(frozen=True, slots=True)
class QuestCard:
    """Задание текущего периода."""

    key: str
    title: str
    description: str
    target: int
    progress: int
    completed: bool
    reward_xp: int
    reward_verses: int


@dataclass(frozen=True, slots=True)
class QuestsCard:
    """Задания игрока: дневные и недельные."""

    daily: list[QuestCard]
    weekly: list[QuestCard]


@dataclass(frozen=True, slots=True)
class BadgeCard:
    """Бейдж игрока."""

    key: str
    title: str
    description: str
    target: int
    progress: int
    unlocked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PrizeCard:
    """Приз сектора колеса."""

    kind: PrizeKind
    amount: int
    multiplier: float | None = None
    matches: int | None = None
    item_key: str | None = None


@dataclass(frozen=True, slots=True)
class WheelSectorCard:
    """Сектор колеса."""

    key: str
    title: str
    chance: float
    prize: PrizeCard


@dataclass(frozen=True, slots=True)
class SpinCard:
    """Сегодняшний результат спина."""

    day: date
    sector_key: str
    prize: PrizeCard
    spun_at: datetime
    repeated: bool


@dataclass(frozen=True, slots=True)
class WheelCard:
    """Колесо на сегодня (``GET /v1/wheel``); ``can_spin`` = включено и сегодня не крутили."""

    enabled: bool
    can_spin: bool
    next_spin_at: datetime | None
    sectors: list[WheelSectorCard]
    today: SpinCard | None = None


@dataclass(frozen=True, slots=True)
class BadgeRef:
    """Бейдж в публичном профиле: получен (``unlocked_at`` всегда есть)."""

    key: str
    title: str
    unlocked_at: datetime


@dataclass(frozen=True, slots=True)
class PublicProgress:
    """Публичная часть прогресса игрока (``GET /v1/users/{id}/progress``)."""

    user_id: UUID
    level: int
    total_xp: int
    badges: list[BadgeRef]


@dataclass(frozen=True, slots=True)
class PlayerBlocks:
    """Блоки профиля чужого игрока."""

    progress: Block[PublicProgress]
    appearance: Block[AppearanceCard] | None = None
    social: Block[PlayerSocialCard] | None = None


@dataclass(frozen=True, slots=True)
class PlayerPage:
    """``GET /v1/pages/players/{user_id}``; ``restricted`` — профиль скрыт от зрителя (5c)."""

    blocks: PlayerBlocks
    restricted: bool = False
    nickname: str | None = None
    """Ник из social (ADR 0069); ``None`` - профиль скрыт, свой профиль или social недоступен."""


@dataclass(frozen=True, slots=True)
class HomeProgressCard:
    """Свёртка прогресса для главной."""

    level: int
    xp_in_level: int
    xp_to_next: int | None
    best_streak: int
    can_spin: bool
    quests_open: int


@dataclass(frozen=True, slots=True)
class ProfileBlocks:
    """Блоки профиля."""

    progress: Block[ProgressCard]
    wallet: Block[WalletCard]
    quests: Block[QuestsCard]
    badges: Block[list[BadgeCard]]
    wheel: Block[WheelCard]
    appearance: Block[AppearanceCard] | None = None
    inventory: Block[InventoryCard] | None = None
    avatar: Block[AvatarCard] | None = None
    events: list[EventArchiveCard] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProfilePage:
    """``GET /v1/pages/profile``."""

    blocks: ProfileBlocks


# ----------------------------------------------------------------------------- 5b: косметика, тема
@dataclass(frozen=True, slots=True)
class CosmeticCard:
    """Предмет косметики (progression ``ItemView``)."""

    id: UUID
    key: str
    slot: str
    rarity: str
    title: str
    media_kind: Literal["static", "animated"]
    asset_url: str
    poster_url: str | None
    level_required: int | None
    price: int | None


@dataclass(frozen=True, slots=True)
class AppearanceSlotCard:
    """Слот оформления: предмет или пусто."""

    slot: str
    item: CosmeticCard | None = None


@dataclass(frozen=True, slots=True)
class AppearanceCard:
    """Оформление: по предмету на слот и своя аватарка (если одобрена)."""

    slots: list[AppearanceSlotCard]
    custom_avatar_url: str | None = None
    custom_avatar_asset_id: UUID | None = None
    """Ассет одобренной аватарки (объект жалобы ``avatar``); ``None`` - нет или старый источник."""


@dataclass(frozen=True, slots=True)
class PlayerAppearanceCard:
    """Оформление игрока по ``user_id`` (матч, профиль)."""

    user_id: UUID
    appearance: AppearanceCard


@dataclass(frozen=True, slots=True)
class InventoryEntryCard:
    """Предмет инвентаря."""

    item: CosmeticCard
    source: str
    equipped: bool
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class InventoryCard:
    """Инвентарь игрока."""

    items: list[InventoryEntryCard]


@dataclass(frozen=True, slots=True)
class AvatarRequestCard:
    """Последняя заявка на свою аватарку."""

    status: Literal["pending", "approved", "rejected"]
    submitted_at: datetime
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class AvatarCard:
    """Своя аватарка: право, заявка, лимиты загрузки."""

    entitled: bool
    request: AvatarRequestCard | None
    max_bytes: int
    formats: list[str]


@dataclass(frozen=True, slots=True)
class ShopEntryCard:
    """Предмет витрины с состоянием для игрока."""

    item: CosmeticCard
    state: Literal["locked", "available", "owned"]


@dataclass(frozen=True, slots=True)
class ShopCatalogCard:
    """Витрина: предметы, баланс и слоты (в порядке появления в ответе)."""

    items: list[ShopEntryCard]
    balance: int
    slots: list[str]


@dataclass(frozen=True, slots=True)
class PackCard:
    """Пак косметики."""

    id: UUID
    key: str
    title: str
    style: str
    items: list[CosmeticCard]
    verses_amount: int
    price_kopecks: int


@dataclass(frozen=True, slots=True)
class ShopBlocks:
    """Блоки витрины; ``packs = None`` — продажа выключена."""

    catalog: Block[ShopCatalogCard]
    packs: Block[list[PackCard]] | None


@dataclass(frozen=True, slots=True)
class ShopPage:
    """``GET /v1/pages/shop``."""

    blocks: ShopBlocks


@dataclass(frozen=True, slots=True)
class ThemeCard:
    """Активная тема: только ключ (ADR 0049)."""

    key: str


@dataclass(frozen=True, slots=True)
class BellCard:
    """Колокольчик: число непрочитанных."""

    unread: int


@dataclass(frozen=True, slots=True)
class ShellPage:
    """``GET /v1/pages/shell``; ``personal`` — запрос пришёл с токеном (кэш ``private``)."""

    theme: ThemeCard
    bell: Block[BellCard] | None
    personal: bool
    sanctions: Block[SanctionsCard] | None = None
    event: EventBannerCard | None = None


# ----------------------------------------------------------------------------- события (5d)
EventStatus = Literal["active", "finished", "stopped"]
FindState = Literal["locked", "open", "found"]


@dataclass(frozen=True, slots=True)
class ActiveEvent:
    """Активный запуск (progression ``/v1/events/active``); ``theme_key`` пуст - без темы."""

    key: str
    title: str
    theme_key: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class EventBannerCard:
    """Баннер события; ``found``/``total`` - только вошедшему игроку."""

    key: str
    title: str
    ends_at: datetime
    found: int | None = None
    total: int | None = None


@dataclass(frozen=True, slots=True)
class EventArchiveCard:
    """Запуск в архиве игрока."""

    key: str
    title: str
    status: EventStatus
    starts_at: datetime
    ends_at: datetime
    found: int
    total: int
    completed: bool


@dataclass(frozen=True, slots=True)
class EventRewardCard:
    """Основная награда события."""

    item_key: str
    title: str
    image_url: str


@dataclass(frozen=True, slots=True)
class EventFindRewardCard:
    """Награда находки."""

    xp: int
    verses: int


@dataclass(frozen=True, slots=True)
class EventFindTriggerCard:
    """Условие открытия находки."""

    type: Literal["server", "ui_spot"]
    page_key: str | None
    metric: str | None
    progress: int | None
    target: int | None
    period: Literal["day", "event"] | None


@dataclass(frozen=True, slots=True)
class EventFindCard:
    """Находка с состоянием игрока."""

    key: str
    number: int
    title: str
    x_pct: float
    y_pct: float
    final: bool
    state: FindState
    diary: str
    reward: EventFindRewardCard
    play_page: str | None
    trigger: EventFindTriggerCard
    found_at: datetime | None


@dataclass(frozen=True, slots=True)
class EventPageData:
    """Страница события из progression."""

    key: str
    title: str
    description: str
    theme_key: str
    status: EventStatus
    starts_at: datetime
    ends_at: datetime
    map_url: str | None
    found: int
    total: int
    completed: bool
    reward_item: EventRewardCard | None
    finds: list[EventFindCard]


@dataclass(frozen=True, slots=True)
class EventPageResult:
    """Страница события и ключ темы экрана (``theme.key``)."""

    page: EventPageData
    theme: ThemeCard


# ----------------------------------------------------------------------------- social (5c)
FriendStatus = Literal["online", "in_match", "offline"]
SanctionKind = Literal["ranked", "chat", "online", "account"]


@dataclass(frozen=True, slots=True)
class PlayerRelation:
    """Отношение зрителя к игроку (social ``/v1/players/{id}/relation``).

    ``profile_visible = False`` одинаков для скрытого профиля, блокировки зрителя и
    неизвестного игрока: различать причины нельзя (нет перечисления аккаунтов).
    """

    profile_visible: bool
    stats_visible: bool
    is_friend: bool
    can_send_request: bool
    blocked_by_me: bool
    nickname: str | None = None
    """Ник - только при видимом профиле (ADR 0069)."""


@dataclass(frozen=True, slots=True)
class PlayerSocialCard:
    """Блок ``social`` профиля игрока."""

    is_friend: bool
    can_send_request: bool
    blocked_by_me: bool


@dataclass(frozen=True, slots=True)
class FriendCard:
    """Друг со статусом (без деталей presence)."""

    user_id: UUID
    nickname: str
    status: FriendStatus


@dataclass(frozen=True, slots=True)
class FriendsCard:
    """Друзья владельца и число входящих заявок."""

    items: list[FriendCard]
    incoming_requests: int


@dataclass(frozen=True, slots=True)
class CommentCard:
    """Комментарий к сборнику; ``can_*`` посчитаны social для зрителя."""

    id: UUID
    author_id: UUID
    author_nickname: str
    body: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool
    can_delete: bool


@dataclass(frozen=True, slots=True)
class CommentsCard:
    """Первая страница комментариев сборника."""

    items: list[CommentCard]
    total: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SanctionCard:
    """Действующая санкция (без данных модератора)."""

    kind: SanctionKind
    expires_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class SanctionsCard:
    """Санкции игрока; пусто — ограничений нет."""

    items: list[SanctionCard]
