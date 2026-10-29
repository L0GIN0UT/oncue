"""HTTP-клиенты game, catalog, identity, collections, library и match по их OpenAPI-контрактам.

Схемы ниже — подмножество ``contracts/openapi/<service>.json``: только поля, нужные экранам,
лишние отбрасываются (источник может добавлять поля без релиза bff). Ответ, не прошедший
разбор, — нарушение контракта: блок гаснет (``UpstreamUnavailableError``), а не 500.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from bff_web_service.application.dto import (
    ActiveEvent,
    AnswerFormat,
    AppearanceCard,
    AppearanceSlotCard,
    AvatarCard,
    AvatarRequestCard,
    BadgeCard,
    BadgeRef,
    BoostCard,
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
    CommentCard,
    CommentsCard,
    CosmeticCard,
    CustomClue,
    DailyRoundStat,
    DailyStatus,
    Difficulty,
    Domain,
    EventArchiveCard,
    EventFindCard,
    EventFindRewardCard,
    EventFindTriggerCard,
    EventPageData,
    EventRewardCard,
    FriendCard,
    InventoryCard,
    InventoryEntryCard,
    Item,
    ItemShelves,
    LadderCard,
    LeaderboardCard,
    LeaderboardEntryCard,
    MatchMode,
    MatchPlayer,
    MatchResult,
    MatchRound,
    Me,
    Mode,
    ModeStart,
    MyMatch,
    MyMatches,
    OnlineChat,
    OnlineRules,
    OpenRoom,
    OpenRooms,
    PackCard,
    Person,
    PersonalRecord,
    PlayerAppearanceCard,
    PlayerRelation,
    Presentation,
    Preset,
    PrizeCard,
    ProgressCard,
    PublicProgress,
    QuestCard,
    QuestsCard,
    QueuePoolCard,
    RatingCard,
    ResultRecord,
    ResultRound,
    Reveal,
    RoomSettings,
    RoundPlayerResult,
    RoundsChoice,
    SanctionCard,
    SanctionsCard,
    SeasonCard,
    SessionResult,
    Share,
    Shelf,
    ShelfItem,
    ShelfItems,
    ShopCatalogCard,
    ShopEntryCard,
    SpinCard,
    StreakCard,
    SubjectPick,
    TierCard,
    WalletCard,
    WeeklyEntryCard,
    WeeklyTopCard,
    WheelCard,
    WheelSectorCard,
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


class _OnlinePlayersIn(_In):
    min: int = Field(ge=2)
    max: int = Field(ge=2)


class _OnlineChatIn(_In):
    emotes: list[str]
    phrases: list[str]
    free_text: bool


class _OnlineIn(_In):
    kind: Literal["duel", "party", "lobby"]
    players: _OnlinePlayersIn
    rounds: int = Field(ge=1)
    level_windows_s: list[int]
    contest_type: str
    ranked: bool
    chat: _OnlineChatIn
    ladder: str | None = None
    max_collections: int | None = Field(default=None, ge=1)
    # Схема лобби — ``$defs/LobbySpec`` конфига режима; bff передаёт её фронту как есть.
    lobby: dict[str, JsonValue] | None = None

    def dto(self) -> OnlineRules:
        return OnlineRules(
            kind=self.kind,
            players_min=self.players.min,
            players_max=self.players.max,
            rounds=self.rounds,
            level_windows_s=list(self.level_windows_s),
            contest_type=self.contest_type,
            ranked=self.ranked,
            chat=OnlineChat(list(self.chat.emotes), list(self.chat.phrases), self.chat.free_text),
            ladder=self.ladder,
            max_collections=self.max_collections,
            lobby=dict(self.lobby) if self.lobby is not None else None,
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
    online: _OnlineIn | None = None

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
            online=self.online.dto() if self.online is not None else None,
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
        if any(m.online is None for m in modes):
            # По контракту у каждого онлайн-режима есть блок ``online``: без него ответ —
            # не тот список (например, game без параметра ``session`` отдал соло-режимы).
            log.warning("bff.contract_violation", service=self._http.service, reason="no_online")
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
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


class _ItemsIn(_In):
    items: list[_ItemIn]


_ITEMS = TypeAdapter(_ItemsIn)


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

    async def get_items(self, item_ids: Sequence[UUID]) -> list[Item]:
        """``GET /v1/items?ids=...``: пачка карточек (не кэшируется - один вызов на пачку)."""
        query = urlencode([("ids", str(i)) for i in item_ids])
        body = await self._http.get(f"/v1/items?{query}", token=None)
        items = _parse(self._http.service, _ITEMS, body).items
        if not {i.id for i in items} <= set(item_ids):
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return [i.dto() for i in items]


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


class _ItemsShelvesIn(_In):
    items: list[_ItemShelvesIn]


_ITEMS_SHELVES = TypeAdapter(_ItemsShelvesIn)


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

    async def items_shelves(self, token: str, media_item_ids: Sequence[UUID]) -> list[ItemShelves]:
        """``GET /v1/items/shelves?ids=...``: полки пачки единиц, запись на каждую."""
        query = urlencode([("ids", str(i)) for i in media_item_ids])
        body = await self._http.get(f"/v1/items/shelves?{query}", token=token)
        page = _parse(self._http.service, _ITEMS_SHELVES, body).items
        if not {p.media_item_id for p in page} <= set(media_item_ids):
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return [ItemShelves(p.media_item_id, list(p.shelves)) for p in page]


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


class _QueuePoolEntryIn(_In):
    ladder: str
    format: Literal["classic", "lite"]
    searching: int = Field(ge=0)

    def dto(self) -> QueuePoolCard:
        return QueuePoolCard(self.ladder, self.format, self.searching)


class _QueuePoolIn(_In):
    items: list[_QueuePoolEntryIn]


_QUEUE_POOL = TypeAdapter(_QueuePoolIn)


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

    async def queue_pool(self, token: str) -> list[QueuePoolCard]:
        """``GET /v1/queue/pool``."""
        body = await self._http.get("/v1/queue/pool", token=token)
        return [e.dto() for e in _parse(self._http.service, _QUEUE_POOL, body).items]


# ----------------------------------------------------------------------------- rating (4b)
class _TierIn(_In):
    key: str
    family: str
    step: int | None = None
    title_key: str

    def dto(self) -> TierCard:
        return TierCard(self.key, self.family, self.step, self.title_key)


class _SeasonIn(_In):
    number: int = Field(ge=1)
    starts_at: datetime
    ends_at: datetime


class _LadderIn(_In):
    key: str
    domain: Domain
    formats: list[Literal["classic", "lite"]]
    tiers: list[_TierIn]
    season: _SeasonIn | None = None

    def dto(self) -> LadderCard:
        season = (
            SeasonCard(self.key, self.season.number, self.season.starts_at, self.season.ends_at)
            if self.season is not None
            else None
        )
        return LadderCard(
            key=self.key,
            domain=self.domain,
            formats=self.formats,
            season=season,
            tiers=[t.dto() for t in self.tiers],
        )


class _LadderListIn(_In):
    items: list[_LadderIn]


class _RatingIn(_In):
    ladder: str
    rating: int | None
    tier: _TierIn | None
    next_tier_at: int | None = None
    calibrating: bool
    calibration_left: int = Field(ge=0)
    matches_played: int = Field(ge=0)
    place: int | None = Field(default=None, ge=1)

    def dto(self) -> RatingCard:
        return RatingCard(
            ladder=self.ladder,
            rating=self.rating,
            tier=self.tier.dto() if self.tier else None,
            next_tier_at=self.next_tier_at,
            calibrating=self.calibrating,
            calibration_left=self.calibration_left,
            matches_played=self.matches_played,
            place=self.place,
        )


class _MyRatingsIn(_In):
    items: list[_RatingIn]


class _LeaderboardEntryIn(_In):
    place: int = Field(ge=1)
    user_id: UUID
    nickname: str
    rating: int
    tier: _TierIn
    matches_played: int = Field(ge=0)

    def dto(self) -> LeaderboardEntryCard:
        return LeaderboardEntryCard(
            self.place,
            self.user_id,
            self.nickname,
            self.rating,
            self.tier.dto(),
            self.matches_played,
        )


class _LeaderboardIn(_In):
    ladder: str
    items: list[_LeaderboardEntryIn]
    next_cursor: str | None = None
    me: _LeaderboardEntryIn | None = None

    def dto(self) -> LeaderboardCard:
        return LeaderboardCard(
            self.ladder,
            [e.dto() for e in self.items],
            self.next_cursor,
            self.me.dto() if self.me else None,
        )


class _WeeklyEntryIn(_In):
    place: int = Field(ge=1)
    user_id: UUID
    nickname: str
    delta: int

    def dto(self) -> WeeklyEntryCard:
        return WeeklyEntryCard(self.place, self.user_id, self.nickname, self.delta)


class _WeeklyIn(_In):
    ladder: str
    week_start: date
    items: list[_WeeklyEntryIn]
    me: _WeeklyEntryIn | None = None

    def dto(self) -> WeeklyTopCard:
        return WeeklyTopCard(
            self.ladder,
            self.week_start,
            [e.dto() for e in self.items],
            self.me.dto() if self.me else None,
        )


_LADDER_LIST = TypeAdapter(_LadderListIn)
_MY_RATINGS = TypeAdapter(_MyRatingsIn)
_LEADERBOARD = TypeAdapter(_LeaderboardIn)
_WEEKLY = TypeAdapter(_WeeklyIn)


class HttpRatingClient:
    """Публичный API rating.

    Ladder-ы, а также лидерборд и топ недели без токена общие для всех: кэшируются на
    ``ttl_s`` (10 с, ``GV_PAGE_CACHE__LEADERBOARD_TTL_S``). С токеном (``me``,
    ``around=me``) и ``/me/ratings`` — личные, мимо кэша.
    """

    def __init__(self, http: UpstreamHttp, *, ttl_s: int, cache_prefix: str) -> None:
        self._http, self._ttl, self._prefix = http, ttl_s, cache_prefix

    async def ladders(self) -> list[LadderCard]:
        """``GET /v1/ladders`` (общий, кэшируется)."""
        key = f"{self._prefix}rating:ladders:v1"
        body, cached = await self._http.get_public("/v1/ladders", cache_key=key)
        page = _parse(self._http.service, _LADDER_LIST, body)
        if not cached:
            await self._http.remember(key, body, self._ttl)
        return [x.dto() for x in page.items]

    async def my_ratings(self, token: str) -> list[RatingCard]:
        """``GET /v1/me/ratings``."""
        body = await self._http.get("/v1/me/ratings", token=token)
        return [x.dto() for x in _parse(self._http.service, _MY_RATINGS, body).items]

    async def leaderboard(
        self, ladder: str, *, token: str | None, cursor: str | None, around_me: bool
    ) -> LeaderboardCard:
        """``GET /v1/ladders/{ladder}/leaderboard``."""
        path = f"/v1/ladders/{ladder}/leaderboard"
        params: dict[str, str] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if around_me:
            params["around"] = "me"
        if token is not None:
            return self._leaderboard(ladder, await self._http.get(path, token=token, params=params))
        key = f"{self._prefix}rating:leaderboard:v1:{ladder}:{cursor or ''}"
        body, cached = await self._http.get_public(path, cache_key=key, params=params)
        board = self._leaderboard(ladder, body)
        if not cached:
            await self._http.remember(key, body, self._ttl)
        return board

    def _leaderboard(self, ladder: str, body: bytes) -> LeaderboardCard:
        board = _parse(self._http.service, _LEADERBOARD, body)
        if board.ladder != ladder:
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return board.dto()

    async def weekly(self, ladder: str, *, token: str | None) -> WeeklyTopCard:
        """``GET /v1/ladders/{ladder}/weekly``."""
        path = f"/v1/ladders/{ladder}/weekly"
        if token is not None:
            return self._weekly(ladder, await self._http.get(path, token=token))
        key = f"{self._prefix}rating:weekly:v1:{ladder}"
        body, cached = await self._http.get_public(path, cache_key=key)
        top = self._weekly(ladder, body)
        if not cached:
            await self._http.remember(key, body, self._ttl)
        return top

    def _weekly(self, ladder: str, body: bytes) -> WeeklyTopCard:
        top = _parse(self._http.service, _WEEKLY, body)
        if top.ladder != ladder:
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return top.dto()


# ----------------------------------------------------------------------------- progression
class _StreakIn(_In):
    domain: Domain
    current: int = Field(ge=0)
    best: int = Field(ge=0)


class _BoostIn(_In):
    multiplier: float = Field(gt=1)
    matches_left: int = Field(ge=1)


class _ProgressIn(_In):
    level: int = Field(ge=1)
    max_level: int = Field(ge=1)
    total_xp: int = Field(ge=0)
    xp_in_level: int = Field(ge=0)
    xp_to_next: int | None = Field(ge=1)
    solo_xp_today: int = Field(ge=0)
    solo_xp_cap: int = Field(ge=0)
    streaks: list[_StreakIn]
    boost: _BoostIn | None

    def dto(self) -> ProgressCard:
        return ProgressCard(
            level=self.level,
            max_level=self.max_level,
            total_xp=self.total_xp,
            xp_in_level=self.xp_in_level,
            xp_to_next=self.xp_to_next,
            solo_xp_today=self.solo_xp_today,
            solo_xp_cap=self.solo_xp_cap,
            streaks=[StreakCard(s.domain, s.current, s.best) for s in self.streaks],
            boost=BoostCard(self.boost.multiplier, self.boost.matches_left) if self.boost else None,
        )


class _WalletIn(_In):
    earned: int = Field(ge=0)
    paid: int = Field(ge=0)
    total: int = Field(ge=0)


class _RewardIn(_In):
    xp: int = Field(ge=0)
    verses: int = Field(ge=0)


class _QuestIn(_In):
    key: str
    title: str
    description: str
    target: int = Field(ge=1)
    progress: int = Field(ge=0)
    completed: bool
    reward: _RewardIn

    def dto(self) -> QuestCard:
        return QuestCard(
            key=self.key,
            title=self.title,
            description=self.description,
            target=self.target,
            progress=self.progress,
            completed=self.completed,
            reward_xp=self.reward.xp,
            reward_verses=self.reward.verses,
        )


class _QuestPeriodIn(_In):
    items: list[_QuestIn]


class _QuestsIn(_In):
    daily: _QuestPeriodIn
    weekly: _QuestPeriodIn


class _BadgeIn(_In):
    key: str
    title: str
    description: str
    target: int = Field(ge=1)
    progress: int = Field(ge=0)
    unlocked_at: datetime | None

    def dto(self) -> BadgeCard:
        return BadgeCard(
            self.key, self.title, self.description, self.target, self.progress, self.unlocked_at
        )


class _BadgesIn(_In):
    items: list[_BadgeIn]


class _PrizeIn(_In):
    kind: Literal["xp", "verses", "boost", "item", "none"]
    amount: int = Field(ge=0)
    multiplier: float | None = None
    matches: int | None = None
    item_key: str | None = None

    def dto(self) -> PrizeCard:
        return PrizeCard(self.kind, self.amount, self.multiplier, self.matches, self.item_key)


class _SectorIn(_In):
    key: str
    title: str
    chance: float = Field(ge=0, le=1)
    prize: _PrizeIn


class _SpinIn(_In):
    day: date
    sector_key: str
    prize: _PrizeIn
    spun_at: datetime
    repeated: bool


class _WheelIn(_In):
    enabled: bool
    sectors: list[_SectorIn]
    today: _SpinIn | None
    next_spin_at: datetime | None

    def dto(self) -> WheelCard:
        return WheelCard(
            enabled=self.enabled,
            can_spin=self.enabled and self.today is None,
            next_spin_at=self.next_spin_at,
            sectors=[
                WheelSectorCard(s.key, s.title, s.chance, s.prize.dto()) for s in self.sectors
            ],
            today=SpinCard(
                self.today.day,
                self.today.sector_key,
                self.today.prize.dto(),
                self.today.spun_at,
                self.today.repeated,
            )
            if self.today
            else None,
        )


class _BadgeRefIn(_In):
    key: str
    title: str
    unlocked_at: datetime


class _PublicProgressIn(_In):
    user_id: UUID
    level: int = Field(ge=1)
    total_xp: int = Field(ge=0)
    badges: list[_BadgeRefIn]


_PUBLIC_PROGRESS = TypeAdapter(_PublicProgressIn)
_PROGRESS = TypeAdapter(_ProgressIn)
_WALLET = TypeAdapter(_WalletIn)
_QUESTS = TypeAdapter(_QuestsIn)
_BADGES = TypeAdapter(_BadgesIn)
_WHEEL = TypeAdapter(_WheelIn)


class HttpProgressionClient:
    """Публичный API progression: всё личное и не кэшируется, кроме активного события.

    Активное событие общее для всех: кэшируется на ``active_event_ttl_s`` (<= 30 с, 5d);
    прогресс игрока для баннера - на ``event_progress_ttl_s`` (<= 30 с, ключ с ``user_id``).
    """

    def __init__(
        self,
        http: UpstreamHttp,
        *,
        active_event_ttl_s: int = 30,
        event_progress_ttl_s: int = 15,
        cache_prefix: str = "bff_web:",
    ) -> None:
        self._http = http
        self._event_ttl, self._prefix = active_event_ttl_s, cache_prefix
        self._progress_ttl = event_progress_ttl_s

    async def active_event(self) -> ActiveEvent | None:
        """``GET /v1/events/active`` (анонимно, общий кэш)."""
        key = f"{self._prefix}progression:events:active:v1"
        body, cached = await self._http.get_public("/v1/events/active", cache_key=key)
        parsed = _parse(self._http.service, _ACTIVE_EVENT, body)
        if not cached:
            await self._http.remember(key, body, self._event_ttl)
        e = parsed.event
        return (
            None if e is None else ActiveEvent(e.key, e.title, e.theme_key, e.starts_at, e.ends_at)
        )

    async def event_page(self, token: str, event_key: str) -> EventPageData:
        """``GET /v1/events/{key}``."""
        body = await self._http.get(f"/v1/events/{event_key}", token=token)
        return _parse(self._http.service, _EVENT_PAGE, body).dto()

    async def event_progress(self, token: str, user_id: UUID, event_key: str) -> EventPageData:
        """Страница события для баннера оболочки: кэш на игрока (``event_progress_ttl_s``)."""
        key = f"{self._prefix}progression:events:page:{user_id}:{event_key}:v1"
        body, cached = await self._http.get_personal_cached(
            f"/v1/events/{event_key}", token=token, cache_key=key
        )
        page = _parse(self._http.service, _EVENT_PAGE, body).dto()
        if not cached:
            await self._http.remember(key, body, self._progress_ttl)
        return page

    async def my_events(self, token: str) -> list[EventArchiveCard]:
        """``GET /v1/me/events``."""
        body = await self._http.get("/v1/me/events", token=token)
        return [
            EventArchiveCard(
                i.key, i.title, i.status, i.starts_at, i.ends_at, i.found, i.total, i.completed
            )
            for i in _parse(self._http.service, _MY_EVENTS, body).items
        ]

    async def progress(self, token: str) -> ProgressCard:
        """``GET /v1/me/progress``."""
        body = await self._http.get("/v1/me/progress", token=token)
        return _parse(self._http.service, _PROGRESS, body).dto()

    async def wallet(self, token: str) -> WalletCard:
        """``GET /v1/me/wallet``."""
        body = await self._http.get("/v1/me/wallet", token=token)
        w = _parse(self._http.service, _WALLET, body)
        return WalletCard(w.earned, w.paid, w.total)

    async def quests(self, token: str) -> QuestsCard:
        """``GET /v1/me/quests`` (дневные и недельные; разовые экран не показывает)."""
        body = await self._http.get("/v1/me/quests", token=token)
        q = _parse(self._http.service, _QUESTS, body)
        return QuestsCard(
            daily=[x.dto() for x in q.daily.items], weekly=[x.dto() for x in q.weekly.items]
        )

    async def badges(self, token: str) -> list[BadgeCard]:
        """``GET /v1/me/badges``."""
        body = await self._http.get("/v1/me/badges", token=token)
        return [x.dto() for x in _parse(self._http.service, _BADGES, body).items]

    async def wheel(self, token: str) -> WheelCard:
        """``GET /v1/wheel``."""
        body = await self._http.get("/v1/wheel", token=token)
        return _parse(self._http.service, _WHEEL, body).dto()

    async def player(self, token: str, user_id: UUID) -> PublicProgress:
        """``GET /v1/users/{id}/progress``."""
        body = await self._http.get(f"/v1/users/{user_id}/progress", token=token)
        p = _parse(self._http.service, _PUBLIC_PROGRESS, body)
        if p.user_id != user_id:
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return PublicProgress(
            p.user_id,
            p.level,
            p.total_xp,
            [BadgeRef(b.key, b.title, b.unlocked_at) for b in p.badges],
        )

    async def shop_items(self, token: str) -> ShopCatalogCard:
        """``GET /v1/shop/items``; слоты витрины — в порядке появления в ответе."""
        body = await self._http.get("/v1/shop/items", token=token)
        c = _parse(self._http.service, _SHOP_CATALOG, body)
        return ShopCatalogCard(
            items=[ShopEntryCard(e.item.dto(), e.state) for e in c.items],
            balance=c.balance,
            slots=list(dict.fromkeys(e.item.slot for e in c.items)),
        )

    async def shop_packs(self, token: str) -> list[PackCard]:
        """``GET /v1/shop/packs`` (при ``billing.enabled=false`` источник отвечает 404)."""
        body = await self._http.get("/v1/shop/packs", token=token)
        return [
            PackCard(
                p.id,
                p.key,
                p.title,
                p.style,
                [i.dto() for i in p.items],
                p.verses_amount,
                p.price_kopecks,
            )
            for p in _parse(self._http.service, _PACKS, body).packs
        ]

    async def inventory(self, token: str) -> InventoryCard:
        """``GET /v1/me/inventory``."""
        body = await self._http.get("/v1/me/inventory", token=token)
        inv = _parse(self._http.service, _INVENTORY, body)
        return InventoryCard(
            [
                InventoryEntryCard(e.item.dto(), e.source, e.equipped, e.expires_at)
                for e in inv.items
            ]
        )

    async def my_appearance(self, token: str) -> AppearanceCard:
        """``GET /v1/me/appearance``: слоты и своя аватарка (единый источник адреса)."""
        body = await self._http.get("/v1/me/appearance", token=token)
        a = _parse(self._http.service, _MY_APPEARANCE, body)
        return AppearanceCard(a.dto(), a.custom_avatar_url, a.custom_avatar_asset_id)

    async def my_avatar(self, token: str) -> AvatarCard:
        """``GET /v1/me/avatar``: право, заявка, лимиты (``current_url`` устарел - не читаем)."""
        body = await self._http.get("/v1/me/avatar", token=token)
        a = _parse(self._http.service, _AVATAR, body)
        request = (
            AvatarRequestCard(a.request.status, a.request.submitted_at, a.request.reason_code)
            if a.request
            else None
        )
        return AvatarCard(a.entitled, request, a.limits.max_bytes, list(a.limits.formats))

    async def players_appearance(
        self, token: str, user_ids: list[UUID]
    ) -> list[PlayerAppearanceCard]:
        """``GET /v1/appearance?user_ids=...`` (повторяющийся параметр; не больше 12)."""
        query = urlencode([("user_ids", str(i)) for i in user_ids])
        body = await self._http.get(f"/v1/appearance?{query}", token=token)
        players = _parse(self._http.service, _PLAYERS_APPEARANCE, body).players
        if not {p.user_id for p in players} <= set(user_ids):
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return [
            PlayerAppearanceCard(
                p.user_id,
                AppearanceCard(p.appearance.dto(), p.custom_avatar_url, p.custom_avatar_asset_id),
            )
            for p in players
        ]


class _ItemViewIn(_In):
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

    def dto(self) -> CosmeticCard:
        return CosmeticCard(
            self.id,
            self.key,
            self.slot,
            self.rarity,
            self.title,
            self.media_kind,
            self.asset_url,
            self.poster_url,
            self.level_required,
            self.price,
        )


class _ShopItemIn(_In):
    item: _ItemViewIn
    state: Literal["locked", "available", "owned"]


class _ShopCatalogIn(_In):
    items: list[_ShopItemIn]
    balance: int = Field(ge=0)


class _PackIn(_In):
    id: UUID
    key: str
    title: str
    style: str
    items: list[_ItemViewIn]
    verses_amount: int = Field(ge=0)
    price_kopecks: int = Field(ge=1)


class _PacksIn(_In):
    packs: list[_PackIn]


class _InventoryEntryIn(_In):
    item: _ItemViewIn
    source: str
    equipped: bool
    expires_at: datetime | None


class _InventoryIn(_In):
    items: list[_InventoryEntryIn]


class _AppearanceSlotIn(_In):
    slot: str
    item: _ItemViewIn | None


class _AppearanceIn(_In):
    slots: list[_AppearanceSlotIn]

    def dto(self) -> list[AppearanceSlotCard]:
        return [AppearanceSlotCard(s.slot, s.item.dto() if s.item else None) for s in self.slots]


class _MyAppearanceIn(_AppearanceIn):
    custom_avatar_url: str | None
    custom_avatar_asset_id: UUID | None


class _PlayerAppearanceIn(_In):
    user_id: UUID
    appearance: _AppearanceIn
    custom_avatar_url: str | None
    custom_avatar_asset_id: UUID | None = None


class _PlayersAppearanceIn(_In):
    players: list[_PlayerAppearanceIn]


class _AvatarRequestIn(_In):
    status: Literal["pending", "approved", "rejected"]
    submitted_at: datetime
    reason_code: str | None


class _AvatarLimitsIn(_In):
    max_bytes: int = Field(ge=1)
    formats: list[str]


class _AvatarIn(_In):
    entitled: bool
    request: _AvatarRequestIn | None
    limits: _AvatarLimitsIn


_SHOP_CATALOG = TypeAdapter(_ShopCatalogIn)
_PACKS = TypeAdapter(_PacksIn)
_INVENTORY = TypeAdapter(_InventoryIn)
_MY_APPEARANCE = TypeAdapter(_MyAppearanceIn)
_PLAYERS_APPEARANCE = TypeAdapter(_PlayersAppearanceIn)
_AVATAR = TypeAdapter(_AvatarIn)


class _EventBannerIn(_In):
    key: str
    title: str
    theme_key: str
    starts_at: datetime
    ends_at: datetime


class _ActiveEventIn(_In):
    event: _EventBannerIn | None


class _FindTriggerIn(_In):
    type: Literal["server", "ui_spot"]
    page_key: str | None = None
    metric: str | None = None
    progress: int | None = None
    target: int | None = None
    period: Literal["day", "event"] | None = None


class _FindRewardIn(_In):
    xp: int
    verses: int


class _EventFindIn(_In):
    key: str
    number: int
    title: str
    x_pct: float
    y_pct: float
    final: bool
    state: Literal["locked", "open", "found"]
    diary: str
    reward: _FindRewardIn
    play_page: str | None = None
    trigger: _FindTriggerIn
    found_at: datetime | None = None
    task_i18n: str | None = None

    def dto(self) -> EventFindCard:
        t = self.trigger
        return EventFindCard(
            self.key,
            self.number,
            self.title,
            self.x_pct,
            self.y_pct,
            self.final,
            self.state,
            self.diary,
            EventFindRewardCard(self.reward.xp, self.reward.verses),
            self.play_page,
            EventFindTriggerCard(t.type, t.page_key, t.metric, t.progress, t.target, t.period),
            self.found_at,
            self.task_i18n,
        )


class _EventRewardIn(_In):
    item_key: str
    title: str
    image_url: str


class _EventPageIn(_In):
    key: str
    title: str
    description: str
    theme_key: str
    status: Literal["active", "finished", "stopped"]
    starts_at: datetime
    ends_at: datetime
    map_url: str | None = None
    found: int
    total: int
    completed: bool
    reward_item: _EventRewardIn | None = None
    finds: list[_EventFindIn]

    def dto(self) -> EventPageData:
        r = self.reward_item
        return EventPageData(
            self.key,
            self.title,
            self.description,
            self.theme_key,
            self.status,
            self.starts_at,
            self.ends_at,
            self.map_url,
            self.found,
            self.total,
            self.completed,
            EventRewardCard(r.item_key, r.title, r.image_url) if r else None,
            [f.dto() for f in self.finds],
        )


class _EventArchiveIn(_In):
    key: str
    title: str
    status: Literal["active", "finished", "stopped"]
    starts_at: datetime
    ends_at: datetime
    found: int
    total: int
    completed: bool


class _MyEventsIn(_In):
    items: list[_EventArchiveIn]


_ACTIVE_EVENT = TypeAdapter(_ActiveEventIn)
_EVENT_PAGE = TypeAdapter(_EventPageIn)
_MY_EVENTS = TypeAdapter(_MyEventsIn)


class _UnreadIn(_In):
    count: int = Field(ge=0)


_UNREAD = TypeAdapter(_UnreadIn)


class HttpNotificationsClient:
    """Публичный API notifications: только личное число непрочитанных, не кэшируется."""

    def __init__(self, http: UpstreamHttp) -> None:
        self._http = http

    async def unread_count(self, token: str) -> int:
        """``GET /v1/notifications/unread-count``."""
        body = await self._http.get("/v1/notifications/unread-count", token=token)
        return _parse(self._http.service, _UNREAD, body).count


# ----------------------------------------------------------------------------- social (5c)
class _RelationIn(_In):
    profile_visible: bool
    stats_visible: bool
    is_friend: bool
    can_send_request: bool
    blocked_by_me: bool
    nickname: str | None = None

    def dto(self) -> PlayerRelation:
        return PlayerRelation(
            self.profile_visible,
            self.stats_visible,
            self.is_friend,
            self.can_send_request,
            self.blocked_by_me,
            self.nickname,
        )


class _RelationItemIn(_RelationIn):
    user_id: UUID


class _RelationsIn(_In):
    items: list[_RelationItemIn]


class _FriendIn(_In):
    user_id: UUID
    nickname: str
    status: Literal["online", "in_match", "offline"]


class _FriendListIn(_In):
    items: list[_FriendIn]


class _RequestsIn(_In):
    incoming: list[JsonValue]


class _CommentIn(_In):
    id: UUID
    author_id: UUID
    author_nickname: str
    body: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool
    can_delete: bool


class _CommentPageIn(_In):
    items: list[_CommentIn]
    total: int = Field(ge=0)
    next_cursor: str | None


class _SanctionIn(_In):
    kind: Literal["ranked", "chat", "online", "account"]
    expires_at: datetime | None
    reason: str


class _SanctionListIn(_In):
    items: list[_SanctionIn]


_RELATION = TypeAdapter(_RelationIn)
_RELATIONS = TypeAdapter(_RelationsIn)
_FRIENDS = TypeAdapter(_FriendListIn)
_REQUESTS = TypeAdapter(_RequestsIn)
_COMMENTS = TypeAdapter(_CommentPageIn)
_SANCTIONS = TypeAdapter(_SanctionListIn)


class HttpSocialClient:
    """Публичный API social: личное и комментарии; не кэшируется (ответ зависит от зрителя)."""

    def __init__(self, http: UpstreamHttp) -> None:
        self._http = http

    async def relation(self, token: str, user_id: UUID) -> PlayerRelation:
        """``GET /v1/players/{id}/relation``."""
        body = await self._http.get(f"/v1/players/{user_id}/relation", token=token)
        return _parse(self._http.service, _RELATION, body).dto()

    async def relations(self, token: str, user_ids: list[UUID]) -> dict[UUID, PlayerRelation]:
        """``GET /v1/players/relations?user_ids=...`` (повторяющийся параметр)."""
        query = urlencode([("user_ids", str(i)) for i in user_ids])
        body = await self._http.get(f"/v1/players/relations?{query}", token=token)
        items = _parse(self._http.service, _RELATIONS, body).items
        if not {i.user_id for i in items} <= set(user_ids):
            raise UpstreamUnavailableError(self._http.service, "contract_violation")
        return {i.user_id: i.dto() for i in items}

    async def friends(self, token: str) -> list[FriendCard]:
        """``GET /v1/me/friends``."""
        body = await self._http.get("/v1/me/friends", token=token)
        listed = _parse(self._http.service, _FRIENDS, body)
        return [FriendCard(f.user_id, f.nickname, f.status) for f in listed.items]

    async def incoming_requests(self, token: str) -> int:
        """``GET /v1/me/friend-requests`` - число входящих."""
        body = await self._http.get("/v1/me/friend-requests", token=token)
        return len(_parse(self._http.service, _REQUESTS, body).incoming)

    async def comments(self, token: str | None, collection_id: UUID, *, limit: int) -> CommentsCard:
        """``GET /v1/collections/{id}/comments?limit=`` (без токена - как аноним)."""
        body = await self._http.get(
            f"/v1/collections/{collection_id}/comments",
            token=token,
            params={"limit": str(limit)},
        )
        page = _parse(self._http.service, _COMMENTS, body)
        return CommentsCard(
            [
                CommentCard(
                    c.id,
                    c.author_id,
                    c.author_nickname,
                    c.body,
                    c.created_at,
                    c.edited_at,
                    c.can_edit,
                    c.can_delete,
                )
                for c in page.items
            ],
            page.total,
            page.next_cursor,
        )

    async def sanctions(self, token: str) -> SanctionsCard:
        """``GET /v1/me/sanctions``."""
        body = await self._http.get("/v1/me/sanctions", token=token)
        listed = _parse(self._http.service, _SANCTIONS, body)
        return SanctionsCard([SanctionCard(s.kind, s.expires_at, s.reason) for s in listed.items])
