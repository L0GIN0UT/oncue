"""Схемы публичного API bff_web (план этапа 2, §2.6; этап 3a, §2.5; этап 3b, §2.5) — экраны
веб-клиента.

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


# ----------------------------------------------------------------------------- сборники (3b)
CollectionSort = Literal["featured", "popular", "new", "likes", "difficulty"]
Difficulty = Literal["easy", "medium", "hard"]
RoundsChoice = Literal[5, 10, 25, "all"]


class CollectionAuthor(BaseModel):
    """Автор сборника (``collections``): пользователь, «Guessverse» или удалённый."""

    kind: Literal["user", "curator", "deleted"]
    user_id: UUID | None = None
    nickname: str | None = None


class CollectionCover(BaseModel):
    """Обложка — пресет (градиент и эмодзи из ``collections /v1/meta``)."""

    preset: str


class CollectionCard(BaseModel):
    """Карточка сборника в каталоге (``collections /v1/collections``), без состава."""

    collection_id: UUID
    domain: Domain
    kind: Literal["ugc", "curated"]
    title: str
    cover: CollectionCover | None = None
    tags: list[str]
    author: CollectionAuthor
    default_mode_key: str
    items_count: int = Field(ge=0)
    likes_count: int = Field(ge=0)
    plays_count: int = Field(ge=0)
    difficulty: Difficulty | None = None
    featured: bool = False
    published_at: datetime


class CollectionResults(BaseModel):
    """Страница карточек по курсору; курсор привязан к сортировке блока."""

    items: list[CollectionCard]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Продолжение: `GET /v1/pages/collections?sort=<сортировка блока>&cursor=…` "
            "(с теми же фильтрами) вернёт блок `results`; null — дальше пусто"
        ),
    )


class CollectionsBlocks(BaseModel):
    """Блоки каталога: без ``q`` и ``cursor`` — витрины, иначе только ``results``.

    Отсутствующий в этом виде страницы блок — ``null``.
    """

    featured: Block[CollectionResults] | None = Field(
        default=None, description="Рекомендуемые (sort=featured)"
    )
    popular: Block[CollectionResults] | None = Field(
        default=None, description="Популярные (sort=popular)"
    )
    new: Block[CollectionResults] | None = Field(default=None, description="Новые (sort=new)")
    results: Block[CollectionResults] | None = Field(
        default=None, description="Поиск (`q`) или продолжение по `cursor` в сортировке `sort`"
    )


class CollectionsPage(BaseModel):
    """``GET /v1/pages/collections``."""

    blocks: CollectionsBlocks


class CustomClueCard(BaseModel):
    """Своя загадка единицы (эмодзи); приходит только автору и админу."""

    emoji: str | None = None


class CollectionItemCard(BaseModel):
    """Единица состава: только если состав открыт субъекту (D6)."""

    position: int = Field(ge=0)
    media_item_id: UUID
    title: str
    year: int | None = None
    available: bool
    custom: CustomClueCard | None = Field(default=None, description="Только автору")


class CollectionDetail(CollectionCard):
    """Сборник целиком (``collections /v1/collections/{id}``)."""

    description: str | None = None
    visibility: Literal["public", "unlisted"]
    status: Literal["published", "hidden"]
    version: int = Field(ge=1)
    items_visible: bool
    items: list[CollectionItemCard] | None = None


class CollectionModeCard(BaseModel):
    """Режим сборника (``game /v1/collections/{id}/modes``)."""

    mode_key: str
    title_key: str
    clue_type: str
    allow_guest: bool
    playable: int = Field(ge=0)
    total: int = Field(ge=0)
    available: bool
    round_options: list[RoundsChoice]


class CollectionMe(BaseModel):
    """Отношение пользователя к сборнику; гостю и анониму — ``me: null``."""

    liked: bool
    favorite: bool
    completed: bool
    is_author: bool = False


class CollectionBlocks(BaseModel):
    """Блоки страницы сборника: недоступный game даёт ``modes: unavailable``."""

    collection: Block[CollectionDetail]
    modes: Block[list[CollectionModeCard]]


class CollectionPage(BaseModel):
    """``GET /v1/pages/collections/{id}``."""

    blocks: CollectionBlocks
    me: CollectionMe | None = None


# ----------------------------------------------------------------------------- медиатека (3b)
class ShelfCard(BaseModel):
    """Полка (``library /v1/shelves``)."""

    ref: str
    shelf_id: UUID | None = None
    kind: Literal["system", "custom"]
    key: str | None = None
    title: str | None = None
    title_key: str | None = None
    domains: list[Domain]
    exclusive_with: list[str] = Field(default_factory=list)
    items_count: int = Field(ge=0)
    updated_at: datetime | None = None


class ShelfItemCard(BaseModel):
    """Единица на полке: карточка из проекции catalog в library."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    year: int | None = None
    persons: list[str] = Field(default_factory=list)
    status: Literal["available", "unavailable"]
    source: Literal["reveal", "results", "card", "collection", "library"]
    added_at: datetime


class LibraryBlocks(BaseModel):
    """Блоки медиатеки."""

    shelves: Block[list[ShelfCard]]


class LibraryPage(BaseModel):
    """``GET /v1/pages/library`` (только пользователь)."""

    blocks: LibraryBlocks


class ShelfItems(BaseModel):
    """Полка и её единицы (страница по курсору)."""

    shelf: ShelfCard
    items: list[ShelfItemCard]
    next_cursor: str | None = None


class LibraryShelfBlocks(BaseModel):
    """Блоки страницы полки."""

    shelf: Block[ShelfItems]


class LibraryShelfPage(BaseModel):
    """``GET /v1/pages/library/{ref}`` (только пользователь)."""

    blocks: LibraryShelfBlocks


# ----------------------------------------------------------------------------- онлайн (4a)
MatchKind = Literal["duel", "party", "lobby"]
MatchFinish = Literal["completed", "forfeit", "tech_win", "aborted"]
PlayerFinish = Literal["completed", "forfeit", "left", "tech_win", "tech_loss"]
DuelOutcome = Literal["win", "loss", "draw"]


class MatchModeCard(BaseModel):
    """Шаблон режима комнаты или матча (``match ModeRef``)."""

    key: str
    version: int = Field(ge=1)
    domain: Domain
    kind: MatchKind
    title_key: str
    clue_type: str
    answer_format: str
    ranked: bool = False


class RoomSettingsCard(BaseModel):
    """Действующие настройки комнаты (шаблон + переопределения хоста)."""

    rounds: int = Field(ge=1)
    level_window_s: int = Field(ge=1, description="Окно уровня, с")
    answer_format: str
    clue_type: str
    players_max: int = Field(ge=2)
    visibility: Literal["public", "private"]
    collection_ids: list[UUID] = Field(default_factory=list)


class OpenRoomCard(BaseModel):
    """Открытое лобби (``match /v1/rooms/open``): вход — по ``code`` напрямую в match."""

    code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{8}$")
    mode: MatchModeCard
    host_nickname: str
    players: int = Field(ge=0)
    players_max: int = Field(ge=2)
    settings: RoomSettingsCard
    created_at: datetime


class OpenRoomsList(BaseModel):
    """Открытые лобби, новые первыми; ``next_cursor`` — для ``match /v1/rooms/open``."""

    items: list[OpenRoomCard]
    next_cursor: str | None = None


class MyMatchCard(BaseModel):
    """Матч в «Моих матчах» (``match /v1/me/matches``)."""

    match_id: UUID
    mode: MatchModeCard
    finished_at: datetime
    finish: MatchFinish
    players: int = Field(ge=1)
    place: int = Field(ge=1)
    score: int = Field(ge=0)
    outcome: DuelOutcome | None = Field(description="Только у дуэли")
    my_finish: PlayerFinish


class MyMatchesList(BaseModel):
    """Мои последние матчи; ``next_cursor`` — для ``match /v1/me/matches``."""

    items: list[MyMatchCard]
    next_cursor: str | None = None


class OnlineBlocks(BaseModel):
    """Блоки хаба онлайна: каждый деградирует независимо."""

    open_rooms: Block[OpenRoomsList]
    my_matches: Block[MyMatchesList]
    modes: Block[list[ModeCard]] = Field(
        description="Онлайн-режимы для создания комнаты (`game /v1/modes?session=online`)"
    )


class OnlinePage(BaseModel):
    """``GET /v1/pages/online``: ``auth_required`` — гость или аноним (D13), ``blocks: null``."""

    status: Literal["ok", "auth_required"]
    blocks: OnlineBlocks | None = None


class MatchPlayerCard(BaseModel):
    """Игрок в итоге матча: место с ничьими (1, 1, 3), ``outcome`` — только у дуэли."""

    user_id: UUID
    nickname: str
    slot: int = Field(ge=0)
    place: int = Field(ge=1)
    score: int = Field(ge=0)
    outcome: DuelOutcome | None
    finish: PlayerFinish


class RoundPlayerResultCard(BaseModel):
    """Результат игрока в раунде."""

    user_id: UUID
    level: int | None = Field(description="Уровень угадывания; null — не угадал")
    partial: bool
    score: int = Field(ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0, description="От открытия уровня")


class MatchRoundCard(BaseModel):
    """Раунд итога: карточка единицы — в блоке ``items`` по ``media_item_id``."""

    index: int = Field(ge=0)
    media_item_id: UUID
    results: list[RoundPlayerResultCard]
    void: bool = Field(default=False, description="Раунд аннулирован (ассет недоступен)")


class MatchSummary(BaseModel):
    """Итог матча (``match /v1/matches/{id}``)."""

    match_id: UUID
    room_code: str
    mode: MatchModeCard
    started_at: datetime
    finished_at: datetime
    finish: MatchFinish
    players: list[MatchPlayerCard]
    rounds: list[MatchRoundCard]
    collection_ids: list[UUID] = Field(default_factory=list)


class ItemShelvesCard(BaseModel):
    """Флаг «в медиатеке»: полки пользователя с единицей (пусто — не сохранена)."""

    media_item_id: UUID
    shelves: list[str]


class MatchBlocks(BaseModel):
    """Блоки экрана итога матча."""

    match: Block[MatchSummary]
    items: Block[list[ItemCard]]
    library: Block[list[ItemShelvesCard]]


class MatchPage(BaseModel):
    """``GET /v1/pages/match/{match_id}`` (только пользователь)."""

    blocks: MatchBlocks
