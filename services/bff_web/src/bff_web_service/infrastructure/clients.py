"""HTTP-клиенты game, catalog, identity, collections, library и match по их OpenAPI-контрактам.

Схемы ниже — подмножество ``contracts/openapi/<service>.json``: только поля, нужные экранам,
лишние отбрасываются (источник может добавлять поля без релиза bff). Ответ, не прошедший
разбор, — нарушение контракта: блок гаснет (``UpstreamUnavailableError``), а не 500.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from bff_web_service.application.dto import (
    AnswerFormat,
    Bracket,
    BracketPick,
    CatalogQuery,
    CollectionAuthor,
    CollectionCard,
    CollectionCover,
    CollectionDetail,
    CollectionItem,
    CollectionList,
    CollectionMe,
    CollectionMode,
    CustomClue,
    DailyRoundStat,
    DailyStatus,
    Difficulty,
    Domain,
    Item,
    ItemShelves,
    MatchMode,
    MatchPlayer,
    MatchResult,
    MatchRound,
    Me,
    Mode,
    ModeStart,
    MyMatch,
    MyMatches,
    OpenRoom,
    OpenRooms,
    Person,
    PersonalRecord,
    Presentation,
    Preset,
    ResultRecord,
    ResultRound,
    Reveal,
    RoomSettings,
    RoundPlayerResult,
    RoundsChoice,
    SessionResult,
    Share,
    Shelf,
    ShelfItem,
    ShelfItems,
    SubjectPick,
)
from bff_web_service.application.ports import UpstreamUnavailableError
from bff_web_service.infrastructure.http import UpstreamHttp

log = structlog.get_logger(__name__)


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# ----------------------------------------------------------------------------- game
class _PresetIn(_In):
    id: str
    title_key: str


class _SubjectPickIn(_In):
    roles: list[str]
    max: int = Field(ge=1)


class _StartIn(_In):
    subject: _SubjectPickIn | None = None

    def dto(self) -> ModeStart:
        subject = self.subject
        return ModeStart(
            subject=SubjectPick(list(subject.roles), subject.max) if subject is not None else None
        )


class _ModeIn(_In):
    key: str
    version: int
    domain: Domain
    title_key: str
    source_type: str
    clue_type: str
    rounds: int
    allow_guest: bool
    presets: list[_PresetIn] | None = None
    answer_format: AnswerFormat = "classic"
    presentation: Presentation = "score"
    start: _StartIn | None = None

    def dto(self) -> Mode:
        return Mode(
            key=self.key,
            version=self.version,
            domain=self.domain,
            title_key=self.title_key,
            source_type=self.source_type,
            clue_type=self.clue_type,
            rounds=self.rounds,
            allow_guest=self.allow_guest,
            presets=[Preset(p.id, p.title_key) for p in self.presets]
            if self.presets is not None
            else None,
            answer_format=self.answer_format,
            presentation=self.presentation,
            start=self.start.dto() if self.start is not None else None,
        )


class _DailyIn(_In):
    mode_key: str
    day: date
    status: Literal["not_started", "in_progress", "finished"]
    max_score: int
    next_reset_at: datetime
    session_id: UUID | None = None
    score: int | None = None

    def dto(self) -> DailyStatus:
        return DailyStatus(
            mode_key=self.mode_key,
            day=self.day,
            status=self.status,
            max_score=self.max_score,
            next_reset_at=self.next_reset_at,
            session_id=self.session_id,
            score=self.score,
        )


class _RevealIn(_In):
    media_item_id: UUID


class _RoundIn(_In):
    index: int
    outcome: Literal["exact", "partial", "miss", "gave_up", "void"]
    level: int | None
    points: int
    reveal: _RevealIn | None


class _StatIn(_In):
    index: int
    exact_pct: float = Field(ge=0, le=100)


class _ShareIn(_In):
    text: str


class _ResultRecordIn(_In):
    best: int
    is_new: bool
    percentile: float | None = Field(default=None, ge=0, le=100)


class _BracketPickIn(_In):
    stage: int
    winner: str
    loser: str


class _BracketIn(_In):
    champion: str
    picks: list[_BracketPickIn]

    def dto(self) -> Bracket:
        return Bracket(
            champion=self.champion,
            picks=[BracketPick(p.stage, p.winner, p.loser) for p in self.picks],
        )


class _ResultIn(_In):
    score: int
    max_score: int
    rounds: list[_RoundIn]
    share: _ShareIn
    daily_stats: list[_StatIn] | None = None
    presentation: Presentation = "score"
    record: _ResultRecordIn | None = None
    bracket: _BracketIn | None = None

    def dto(self) -> SessionResult:
        return SessionResult(
            score=self.score,
            max_score=self.max_score,
            rounds=[
                ResultRound(
                    index=r.index,
                    outcome=r.outcome,
                    level=r.level,
                    points=r.points,
                    reveal=Reveal(r.reveal.media_item_id) if r.reveal else None,
                )
                for r in self.rounds
            ],
            share=Share(self.share.text),
            daily_stats=[DailyRoundStat(s.index, s.exact_pct) for s in self.daily_stats]
            if self.daily_stats is not None
            else None,
            presentation=self.presentation,
            record=ResultRecord(self.record.best, self.record.is_new, self.record.percentile)
            if self.record is not None
            else None,
            bracket=self.bracket.dto() if self.bracket is not None else None,
        )


class _RecordIn(_In):
    mode_key: str
    best: int
    achieved_at: datetime
    percentile: float | None = Field(default=None, ge=0, le=100)

    def dto(self) -> PersonalRecord:
        return PersonalRecord(
            mode_key=self.mode_key,
            best=self.best,
            achieved_at=self.achieved_at,
            percentile=self.percentile,
        )


class _CollectionModeIn(_In):
    mode_key: str
    title_key: str
    clue_type: str
    allow_guest: bool
    playable: int = Field(ge=0)
    total: int = Field(ge=0)
    available: bool
    round_options: list[RoundsChoice]

    def dto(self) -> CollectionMode:
        return CollectionMode(
            mode_key=self.mode_key,
            title_key=self.title_key,
            clue_type=self.clue_type,
            allow_guest=self.allow_guest,
            playable=self.playable,
            total=self.total,
            available=self.available,
            round_options=list(self.round_options),
        )


_MODES = TypeAdapter(list[_ModeIn])
_COLLECTION_MODES = TypeAdapter(list[_CollectionModeIn])
_DAILIES = TypeAdapter(list[_DailyIn])
_RECORDS = TypeAdapter(list[_RecordIn])
_RESULT = TypeAdapter(_ResultIn)


def _parse[T](service: str, adapter: TypeAdapter[T], body: bytes) -> T:
    try:
        return adapter.validate_json(body)
    except ValidationError as exc:
        log.warning("bff.contract_violation", service=service, errors=exc.error_count())
        raise UpstreamUnavailableError(service, "contract_violation") from exc


class HttpGameClient:
    """Публичный API game (``/v1``).

    Список режимов публичный и одинаков для всех — кэшируется (``modes_ttl_s``); дейлики,
    рекорды и итог личные и не кэшируются никогда.
    """

    def __init__(
        self,
        http: UpstreamHttp,
        *,
        modes_ttl_s: int,
        cache_prefix: str,
        collection_modes_ttl_s: int = 30,
    ) -> None:
        self._http, self._ttl = http, modes_ttl_s
        self._collection_modes_ttl, self._prefix = collection_modes_ttl_s, cache_prefix
        self._modes_key = f"{cache_prefix}game:modes:v1"
        self._online_modes_key = f"{cache_prefix}game:modes:v1:online"

    async def list_modes(self) -> list[Mode]:
        """Опубликованные режимы всех доменов."""
        body, cached = await self._http.get_public("/v1/modes", cache_key=self._modes_key)
        modes = _parse(self._http.service, _MODES, body)
        if not cached:
            await self._http.remember(self._modes_key, body, self._ttl)
        return [m.dto() for m in modes]

    async def online_modes(self) -> list[Mode]:
        """Онлайн-режимы (``/v1/modes?session=online``): публичные, кэшируются как витрина."""
        body, cached = await self._http.get_public(
            "/v1/modes", cache_key=self._online_modes_key, params={"session": "online"}
        )
        modes = _parse(self._http.service, _MODES, body)
        if not cached:
            await self._http.remember(self._online_modes_key, body, self._ttl)
        return [m.dto() for m in modes]

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        """Плановые режимы периода (дейлики и недельная сетка) субъекта токена."""
        params = {"domain": domain} if domain is not None else None
        body = await self._http.get("/v1/daily", token=token, params=params)
        return [d.dto() for d in _parse(self._http.service, _DAILIES, body)]

    async def session_result(self, token: str, session_id: UUID) -> SessionResult:
        """Итог своей завершённой сессии."""
        body = await self._http.get(f"/v1/sessions/{session_id}/result", token=token)
        return _parse(self._http.service, _RESULT, body).dto()

    async def records(self, token: str) -> list[PersonalRecord]:
        """Личные рекорды субъекта токена (все режимы)."""
        body = await self._http.get("/v1/me/records", token=token)
        return [r.dto() for r in _parse(self._http.service, _RECORDS, body)]

    async def collection_modes(self, collection_id: UUID) -> list[CollectionMode]:
        """Режимы сборника: ответ не зависит от субъекта — вызов без токена, через кэш."""
        key = f"{self._prefix}game:collection_modes:v1:{collection_id}"
        body, cached = await self._http.get_public(
            f"/v1/collections/{collection_id}/modes", cache_key=key
        )
        modes = _parse(self._http.service, _COLLECTION_MODES, body)
        if not cached:
            await self._http.remember(key, body, self._collection_modes_ttl)
        return [m.dto() for m in modes]


# ----------------------------------------------------------------------------- catalog
class _PersonIn(_In):
    name: str
    role: str


class _ItemIn(_In):
    id: UUID
    domain: Domain
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    persons: list[_PersonIn] = Field(default_factory=list)

    def dto(self) -> Item:
        return Item(
            id=self.id,
            domain=self.domain,
            kind=self.kind,
            title=self.title,
            original_title=self.original_title,
            year=self.year,
            genres=list(self.genres),
            persons=[Person(p.name, p.role) for p in self.persons],
        )


_ITEM = TypeAdapter(_ItemIn)


class HttpCatalogClient:
    """Публичный API catalog: карточки единиц кэшируются (ключ без пользователя)."""

    def __init__(self, http: UpstreamHttp, *, item_ttl_s: int, cache_prefix: str) -> None:
        self._http, self._ttl, self._prefix = http, item_ttl_s, cache_prefix

    async def get_item(self, item_id: UUID) -> Item:
        """Публичная карточка единицы."""
        key = f"{self._prefix}catalog:item:v1:{item_id}"
        body, cached = await self._http.get_public(f"/v1/items/{item_id}", cache_key=key)
        item = _parse(self._http.service, _ITEM, body)
        if item.id != item_id:
            # Чужая карточка под нашим ключом закрепилась бы в кэше на весь TTL.
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        if not cached:
            await self._http.remember(key, body, self._ttl)
        return item.dto()


# ----------------------------------------------------------------------------- identity
class _MeIn(_In):
    id: UUID
    display_name: str


_ME = TypeAdapter(_MeIn)


class HttpIdentityClient:
    """Публичный API identity: профиль субъекта токена (не кэшируется)."""

    def __init__(self, http: UpstreamHttp) -> None:
        self._http = http

    async def me(self, token: str) -> Me:
        """``GET /v1/me``; гость получает 403 ``auth.guest_forbidden``."""
        body = await self._http.get("/v1/me", token=token)
        me = _parse(self._http.service, _ME, body)
        return Me(id=me.id, display_name=me.display_name)


# ----------------------------------------------------------------------------- collections
class _AuthorIn(_In):
    kind: Literal["user", "curator", "deleted"]
    user_id: UUID | None = None
    nickname: str | None = None


class _CoverIn(_In):
    preset: str


class _CardIn(_In):
    collection_id: UUID
    domain: Domain
    kind: Literal["ugc", "curated"]
    title: str
    cover: _CoverIn | None = None
    tags: list[str]
    author: _AuthorIn
    default_mode_key: str
    items_count: int = Field(ge=0)
    likes_count: int = Field(ge=0)
    plays_count: int = Field(ge=0)
    difficulty: Difficulty | None = None
    featured: bool = False
    published_at: datetime

    def author_dto(self) -> CollectionAuthor:
        return CollectionAuthor(self.author.kind, self.author.user_id, self.author.nickname)

    def cover_dto(self) -> CollectionCover | None:
        return CollectionCover(self.cover.preset) if self.cover is not None else None

    def dto(self) -> CollectionCard:
        return CollectionCard(
            collection_id=self.collection_id,
            domain=self.domain,
            kind=self.kind,
            title=self.title,
            cover=self.cover_dto(),
            tags=list(self.tags),
            author=self.author_dto(),
            default_mode_key=self.default_mode_key,
            items_count=self.items_count,
            likes_count=self.likes_count,
            plays_count=self.plays_count,
            difficulty=self.difficulty,
            featured=self.featured,
            published_at=self.published_at,
        )


class _CollectionListIn(_In):
    items: list[_CardIn]
    next_cursor: str | None = None

    def dto(self) -> CollectionList:
        return CollectionList([c.dto() for c in self.items], self.next_cursor)


class _CustomIn(_In):
    emoji: str | None = None


class _CollectionItemIn(_In):
    position: int = Field(ge=0)
    media_item_id: UUID
    title: str
    year: int | None = None
    available: bool
    custom: _CustomIn | None = None

    def dto(self) -> CollectionItem:
        return CollectionItem(
            position=self.position,
            media_item_id=self.media_item_id,
            title=self.title,
            available=self.available,
            year=self.year,
            custom=CustomClue(self.custom.emoji) if self.custom is not None else None,
        )


class _CollectionMeIn(_In):
    liked: bool
    favorite: bool
    completed: bool
    is_author: bool = False


class _DetailIn(_CardIn):
    description: str | None = None
    visibility: Literal["public", "unlisted"]
    status: Literal["published", "hidden"]
    version: int = Field(ge=1)
    items_visible: bool
    items: list[_CollectionItemIn] | None = None
    me: _CollectionMeIn | None = None

    def detail(self) -> CollectionDetail:
        me = self.me
        return CollectionDetail(
            collection_id=self.collection_id,
            domain=self.domain,
            kind=self.kind,
            title=self.title,
            cover=self.cover_dto(),
            tags=list(self.tags),
            author=self.author_dto(),
            default_mode_key=self.default_mode_key,
            items_count=self.items_count,
            likes_count=self.likes_count,
            plays_count=self.plays_count,
            difficulty=self.difficulty,
            featured=self.featured,
            published_at=self.published_at,
            description=self.description,
            visibility=self.visibility,
            status=self.status,
            version=self.version,
            items_visible=self.items_visible,
            items=[i.dto() for i in self.items] if self.items is not None else None,
            me=CollectionMe(me.liked, me.favorite, me.completed, me.is_author)
            if me is not None
            else None,
        )


_COLLECTION_LIST = TypeAdapter(_CollectionListIn)
_DETAIL = TypeAdapter(_DetailIn)


def catalog_params(query: CatalogQuery) -> dict[str, str]:
    """Параметры запроса каталога (без пустых) в стабильном порядке — из них же ключ кэша."""
    raw: dict[str, str | None] = {
        "q": query.q,
        "domain": query.domain,
        "mode": query.mode,
        "difficulty": query.difficulty,
        "curated": None if query.curated is None else str(query.curated).lower(),
        "sort": query.sort,
        "cursor": query.cursor,
        "limit": str(query.limit),
    }
    return {k: v for k, v in sorted(raw.items()) if v is not None}


class HttpCollectionsClient:
    """Публичный API collections.

    Каталог одинаков для всех и запрашивается без токена; витрины (без ``q`` и курсора)
    кэшируются на ``catalog_ttl_s``. Сборник — с токеном субъекта (``items``/``me``) и не
    кэшируется.
    """

    def __init__(self, http: UpstreamHttp, *, catalog_ttl_s: int, cache_prefix: str) -> None:
        self._http, self._ttl, self._prefix = http, catalog_ttl_s, cache_prefix

    async def catalog(self, query: CatalogQuery) -> CollectionList:
        """``GET /v1/collections``."""
        params = catalog_params(query)
        if query.q is not None or query.cursor is not None:
            # Поиск и пролистывание не кэшируем: ключей слишком много, пользы мало.
            body = await self._http.get("/v1/collections", token=None, params=params)
            return _parse(self._http.service, _COLLECTION_LIST, body).dto()
        key = f"{self._prefix}collections:catalog:v1:{urlencode(params)}"
        body, cached = await self._http.get_public("/v1/collections", cache_key=key, params=params)
        page = _parse(self._http.service, _COLLECTION_LIST, body)
        if not cached:
            await self._http.remember(key, body, self._ttl)
        return page.dto()

    async def detail(self, token: str | None, collection_id: UUID) -> CollectionDetail:
        """``GET /v1/collections/{id}`` глазами субъекта токена."""
        body = await self._http.get(f"/v1/collections/{collection_id}", token=token)
        detail = _parse(self._http.service, _DETAIL, body)
        if detail.collection_id != collection_id:
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return detail.detail()


# ----------------------------------------------------------------------------- library
class _ShelfIn(_In):
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

    def dto(self) -> Shelf:
        return Shelf(
            ref=self.ref,
            shelf_id=self.shelf_id,
            kind=self.kind,
            key=self.key,
            title=self.title,
            title_key=self.title_key,
            domains=list(self.domains),
            exclusive_with=list(self.exclusive_with),
            items_count=self.items_count,
            updated_at=self.updated_at,
        )


class _ShelfItemIn(_In):
    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    year: int | None = None
    persons: list[str] = Field(default_factory=list)
    status: Literal["available", "unavailable"]
    source: Literal["reveal", "results", "card", "collection", "library"]
    added_at: datetime

    def dto(self) -> ShelfItem:
        return ShelfItem(
            media_item_id=self.media_item_id,
            domain=self.domain,
            kind=self.kind,
            title=self.title,
            year=self.year,
            persons=list(self.persons),
            status=self.status,
            source=self.source,
            added_at=self.added_at,
        )


class _ShelfItemsIn(_In):
    shelf: _ShelfIn
    items: list[_ShelfItemIn]
    next_cursor: str | None = None


class _ItemShelvesIn(_In):
    media_item_id: UUID
    shelves: list[str]


_SHELVES = TypeAdapter(list[_ShelfIn])
_SHELF_ITEMS = TypeAdapter(_ShelfItemsIn)
_ITEM_SHELVES = TypeAdapter(_ItemShelvesIn)


class HttpLibraryClient:
    """Публичный API library: всё личное, ничего не кэшируется."""

    def __init__(self, http: UpstreamHttp) -> None:
        self._http = http

    async def shelves(self, token: str) -> list[Shelf]:
        """``GET /v1/shelves``."""
        body = await self._http.get("/v1/shelves", token=token)
        return [s.dto() for s in _parse(self._http.service, _SHELVES, body)]

    async def shelf_items(
        self, token: str, shelf_ref: str, *, cursor: str | None, limit: int
    ) -> ShelfItems:
        """``GET /v1/shelves/{ref}/items`` (``ref`` уже проверен по шаблону на входе)."""
        params = {"limit": str(limit)}
        if cursor is not None:
            params["cursor"] = cursor
        body = await self._http.get(f"/v1/shelves/{shelf_ref}/items", token=token, params=params)
        page = _parse(self._http.service, _SHELF_ITEMS, body)
        return ShelfItems(
            shelf=page.shelf.dto(),
            items=[i.dto() for i in page.items],
            next_cursor=page.next_cursor,
        )

    async def item_shelves(self, token: str, media_item_id: UUID) -> ItemShelves:
        """``GET /v1/items/{id}/shelves``: полки пользователя с этой единицей."""
        body = await self._http.get(f"/v1/items/{media_item_id}/shelves", token=token)
        page = _parse(self._http.service, _ITEM_SHELVES, body)
        if page.media_item_id != media_item_id:
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return ItemShelves(media_item_id, list(page.shelves))


# ----------------------------------------------------------------------------- match
_MatchKindIn = Literal["duel", "party", "lobby"]
_MatchFinishIn = Literal["completed", "forfeit", "tech_win", "aborted"]
_PlayerFinishIn = Literal["completed", "forfeit", "left", "tech_win", "tech_loss"]
_OutcomeIn = Literal["win", "loss", "draw"]


class _MatchModeIn(_In):
    key: str
    version: int = Field(ge=1)
    domain: Domain
    kind: _MatchKindIn
    title_key: str
    clue_type: str
    answer_format: str
    ranked: bool = False

    def dto(self) -> MatchMode:
        return MatchMode(
            key=self.key,
            version=self.version,
            domain=self.domain,
            kind=self.kind,
            title_key=self.title_key,
            clue_type=self.clue_type,
            answer_format=self.answer_format,
            ranked=self.ranked,
        )


class _RoomSettingsIn(_In):
    rounds: int = Field(ge=1)
    level_window_s: int = Field(ge=1)
    answer_format: str
    clue_type: str
    players_max: int = Field(ge=2)
    visibility: Literal["public", "private"]
    collection_ids: list[UUID] = Field(default_factory=list)


class _OpenRoomIn(_In):
    code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{8}$")
    mode: _MatchModeIn
    host_nickname: str
    players: int = Field(ge=0)
    players_max: int = Field(ge=2)
    settings: _RoomSettingsIn
    created_at: datetime

    def dto(self) -> OpenRoom:
        st = self.settings
        return OpenRoom(
            code=self.code,
            mode=self.mode.dto(),
            host_nickname=self.host_nickname,
            players=self.players,
            players_max=self.players_max,
            settings=RoomSettings(
                rounds=st.rounds,
                level_window_s=st.level_window_s,
                answer_format=st.answer_format,
                clue_type=st.clue_type,
                players_max=st.players_max,
                visibility=st.visibility,
                collection_ids=list(st.collection_ids),
            ),
            created_at=self.created_at,
        )


class _OpenRoomsIn(_In):
    items: list[_OpenRoomIn]
    next_cursor: str | None = None


class _MyMatchIn(_In):
    match_id: UUID
    mode: _MatchModeIn
    finished_at: datetime
    finish: _MatchFinishIn
    players: int = Field(ge=1)
    place: int = Field(ge=1)
    score: int = Field(ge=0)
    outcome: _OutcomeIn | None
    my_finish: _PlayerFinishIn

    def dto(self) -> MyMatch:
        return MyMatch(
            match_id=self.match_id,
            mode=self.mode.dto(),
            finished_at=self.finished_at,
            finish=self.finish,
            players=self.players,
            place=self.place,
            score=self.score,
            outcome=self.outcome,
            my_finish=self.my_finish,
        )


class _MyMatchesIn(_In):
    items: list[_MyMatchIn]
    next_cursor: str | None = None


class _ResultPlayerIn(_In):
    user_id: UUID
    nickname: str
    slot: int = Field(ge=0)
    place: int = Field(ge=1)
    score: int = Field(ge=0)
    outcome: _OutcomeIn | None
    finish: _PlayerFinishIn


class _RoundPlayerIn(_In):
    user_id: UUID
    level: int | None
    partial: bool
    score: int = Field(ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0)


class _ResultRoundIn(_In):
    index: int = Field(ge=0)
    media_item_id: UUID
    results: list[_RoundPlayerIn]
    void: bool = False


class _MatchResultIn(_In):
    match_id: UUID
    room_code: str
    mode: _MatchModeIn
    started_at: datetime
    finished_at: datetime
    finish: _MatchFinishIn
    players: list[_ResultPlayerIn]
    rounds: list[_ResultRoundIn]
    collection_ids: list[UUID] = Field(default_factory=list)

    def dto(self) -> MatchResult:
        return MatchResult(
            match_id=self.match_id,
            room_code=self.room_code,
            mode=self.mode.dto(),
            started_at=self.started_at,
            finished_at=self.finished_at,
            finish=self.finish,
            players=[
                MatchPlayer(
                    user_id=p.user_id,
                    nickname=p.nickname,
                    slot=p.slot,
                    place=p.place,
                    score=p.score,
                    outcome=p.outcome,
                    finish=p.finish,
                )
                for p in self.players
            ],
            rounds=[
                MatchRound(
                    index=r.index,
                    media_item_id=r.media_item_id,
                    results=[
                        RoundPlayerResult(
                            user_id=x.user_id,
                            level=x.level,
                            partial=x.partial,
                            score=x.score,
                            elapsed_ms=x.elapsed_ms,
                        )
                        for x in r.results
                    ],
                    void=r.void,
                )
                for r in self.rounds
            ],
            collection_ids=list(self.collection_ids),
        )


_OPEN_ROOMS = TypeAdapter(_OpenRoomsIn)
_MY_MATCHES = TypeAdapter(_MyMatchesIn)
_MATCH_RESULT = TypeAdapter(_MatchResultIn)


class HttpMatchClient:
    """Публичный API match: всё только пользователю (D13), ничего не кэшируется."""

    def __init__(self, http: UpstreamHttp) -> None:
        self._http = http

    async def open_rooms(self, token: str, *, limit: int) -> OpenRooms:
        """``GET /v1/rooms/open``."""
        body = await self._http.get("/v1/rooms/open", token=token, params={"limit": str(limit)})
        page = _parse(self._http.service, _OPEN_ROOMS, body)
        return OpenRooms([r.dto() for r in page.items], page.next_cursor)

    async def my_matches(self, token: str, *, limit: int) -> MyMatches:
        """``GET /v1/me/matches``."""
        body = await self._http.get("/v1/me/matches", token=token, params={"limit": str(limit)})
        page = _parse(self._http.service, _MY_MATCHES, body)
        return MyMatches([m.dto() for m in page.items], page.next_cursor)

    async def match(self, token: str, match_id: UUID) -> MatchResult:
        """``GET /v1/matches/{id}``."""
        body = await self._http.get(f"/v1/matches/{match_id}", token=token)
        result = _parse(self._http.service, _MATCH_RESULT, body)
        if result.match_id != match_id:
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return result.dto()
