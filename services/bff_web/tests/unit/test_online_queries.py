"""Экраны онлайна: хаб и итог матча — гость, деградация блоков, отказы match (этап 4a, §8)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    ANONYMOUS,
    AppearanceCard,
    CollectionMode,
    DailyStatus,
    Domain,
    Item,
    ItemShelves,
    LadderCard,
    LeaderboardCard,
    LeaderboardEntryCard,
    MatchMode,
    MatchPlayer,
    MatchResult,
    MatchRound,
    Mode,
    MyMatches,
    OnlineChat,
    OnlineRules,
    OnlineStatus,
    OpenRoom,
    OpenRooms,
    PersonalRecord,
    PlayerAppearanceCard,
    PlayerLevel,
    QueuePoolCard,
    RatingCard,
    RoomLobby,
    RoomPlayer,
    RoomSettings,
    SeasonCard,
    SessionResult,
    Shelf,
    ShelfItems,
    SubjectKind,
    TierCard,
    Viewer,
    WeeklyEntryCard,
    WeeklyTopCard,
)
from bff_web_service.application.ports import (
    RatingClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries import (
    GetLeaderboardsPage,
    GetMatchPage,
    GetOnlinePage,
    GetRoomPage,
)
from bff_web_service.domain import BlockStatus

from gv_core.errors import (
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
MATCH_ID = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000d01")
ITEM_A = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a1")
ITEM_B = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a2")
USER = Viewer(token="user-token", kind=SubjectKind.USER, verified=True)
GUEST = Viewer(token="guest-token", kind=SubjectKind.GUEST, verified=True)
UNVERIFIED = Viewer(token="some-token")
DOWN = UpstreamUnavailableError("match", "timeout")
GUEST_REJECTED = UpstreamRejectedError("match", 403, "auth.guest_forbidden")
MODE = MatchMode(
    key="film.duel.frame",
    version=1,
    domain="film",
    kind="duel",
    title_key="modes.film.duel.frame.title",
    clue_type="frame_progressive",
    answer_format="classic",
)
ONLINE_MODE = Mode(
    key="film.duel.frame",
    version=1,
    domain="film",
    title_key="modes.film.duel.frame.title",
    source_type="random",
    clue_type="frame_progressive",
    rounds=7,
    allow_guest=False,
    online=OnlineRules(
        kind="duel",
        players_min=2,
        players_max=2,
        rounds=7,
        level_windows_s=[15, 15, 15],
        contest_type="race",
        ranked=False,
        chat=OnlineChat(["match.emotes.gg"], [], free_text=False),
    ),
)


def result(*items: UUID) -> MatchResult:
    return MatchResult(
        match_id=MATCH_ID,
        room_code="K7M2QX9P",
        mode=MODE,
        started_at=AT,
        finished_at=AT,
        finish="completed",
        players=[],
        rounds=[MatchRound(index=i, media_item_id=m, results=[]) for i, m in enumerate(items)],
    )


def item(item_id: UUID) -> Item:
    return Item(item_id, "film", "movie", f"t-{item_id.hex[-2:]}", None, 1997, [], [])


@dataclass
class FakeMatch:
    rooms: OpenRooms | Exception = field(default_factory=lambda: OpenRooms([], None))
    matches: MyMatches | Exception = field(default_factory=lambda: MyMatches([], None))
    outcome: MatchResult | Exception = field(default_factory=lambda: result(ITEM_A, ITEM_B))
    pool: list[QueuePoolCard] | Exception = field(
        default_factory=lambda: [QueuePoolCard("film", "classic", 3)]
    )
    lobby: RoomLobby | Exception | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def room(self, token: str, code: str) -> RoomLobby:
        self.calls.append(("room", token, code))
        if self.lobby is None:
            raise AssertionError("комнату этот тест не читает")
        if isinstance(self.lobby, Exception):
            raise self.lobby
        return self.lobby

    async def queue_pool(self, token: str) -> list[QueuePoolCard]:
        self.calls.append(("pool", token))
        if isinstance(self.pool, Exception):
            raise self.pool
        return self.pool

    async def open_rooms(self, token: str, *, limit: int) -> OpenRooms:
        self.calls.append(("rooms", token, str(limit)))
        if isinstance(self.rooms, Exception):
            raise self.rooms
        return self.rooms

    async def my_matches(self, token: str, *, limit: int) -> MyMatches:
        self.calls.append(("matches", token, str(limit)))
        if isinstance(self.matches, Exception):
            raise self.matches
        return self.matches

    async def match(self, token: str, match_id: UUID) -> MatchResult:
        self.calls.append(("match", token, str(match_id)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@dataclass
class FakeGame:
    online: list[Mode] | Exception = field(default_factory=lambda: [ONLINE_MODE])
    calls: int = 0

    async def online_modes(self) -> list[Mode]:
        self.calls += 1
        if isinstance(self.online, Exception):
            raise self.online
        return self.online

    async def list_modes(self) -> list[Mode]:
        raise AssertionError("хаб онлайна не читает витрину соло")

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        raise AssertionError("хаб онлайна не читает дейлики")

    async def records(self, token: str) -> list[PersonalRecord]:
        raise AssertionError("хаб онлайна не читает рекорды")

    async def session_result(self, token: str, session_id: UUID) -> SessionResult:
        raise AssertionError("хаб онлайна не читает итог соло")

    async def collection_modes(self, collection_id: UUID) -> list[CollectionMode]:
        raise AssertionError("хаб онлайна не читает режимы сборника")


@dataclass
class FakeCatalog:
    items: dict[UUID, Item | Exception] = field(default_factory=dict)
    batches: list[list[UUID]] = field(default_factory=list)

    async def get_item(self, item_id: UUID) -> Item:
        raise AssertionError("итог матча берёт карточки пачкой")

    async def get_items(self, item_ids: Sequence[UUID]) -> list[Item]:
        self.batches.append(list(item_ids))
        found: list[Item] = []
        for item_id in reversed(item_ids):  # порядок ответа источника не гарантирован
            value = self.items.get(item_id, item(item_id))
            if isinstance(value, UpstreamRejectedError) and value.status == 404:
                continue  # неизвестна или заблокирована - в пачке отсутствует
            if isinstance(value, Exception):
                raise value
            found.append(value)
        return found


@dataclass
class FakeLibrary:
    flags: dict[UUID, list[str] | Exception] = field(default_factory=dict)
    calls: list[list[UUID]] = field(default_factory=list)

    async def item_shelves(self, token: str, media_item_id: UUID) -> ItemShelves:
        raise AssertionError("итог матча берёт полки пачкой")

    async def items_shelves(self, token: str, media_item_ids: Sequence[UUID]) -> list[ItemShelves]:
        self.calls.append(list(media_item_ids))
        found: list[ItemShelves] = []
        for item_id in media_item_ids:
            value = self.flags.get(item_id, [])
            if isinstance(value, UpstreamRejectedError) and value.status == 404:
                continue  # источник пропустил единицу - считаем «нигде не лежит»
            if isinstance(value, Exception):
                raise value
            found.append(ItemShelves(item_id, value))
        return found

    async def shelves(self, token: str) -> list[Shelf]:
        raise AssertionError("итог матча не читает полки")

    async def shelf_items(
        self, token: str, shelf_ref: str, *, cursor: str | None, limit: int
    ) -> ShelfItems:
        raise AssertionError("итог матча не читает полку")


def online(
    match: FakeMatch,
    game: FakeGame | None = None,
    rating: RatingClient | None = None,
    *,
    weekly_limit: int = 2,
) -> GetOnlinePage:
    return GetOnlinePage(
        match,
        game or FakeGame(),
        rating or FakeRating(),
        concurrency=10,
        rooms_limit=20,
        matches_limit=10,
        weekly_limit=weekly_limit,
    )


@dataclass
class FakeAppearance:
    """progression для итога матча: только оформление игроков, пачками."""

    fail: Exception | None = None
    reverse: bool = False
    calls: list[list[UUID]] = field(default_factory=list)

    async def players_appearance(self, token: str, user_ids: list[UUID]) -> list[object]:
        self.calls.append(list(user_ids))
        if self.fail is not None:
            raise self.fail
        cards = [PlayerAppearanceCard(u, AppearanceCard(slots=[])) for u in user_ids]
        return cards[::-1] if self.reverse else cards


def match_page(
    match: FakeMatch,
    catalog: FakeCatalog | None = None,
    library: FakeLibrary | None = None,
    *,
    concurrency: int = 10,
    deadline_s: float = 5.0,
    progression: FakeAppearance | None = None,
    batch: int = 12,
    items_batch: int = 50,
) -> GetMatchPage:
    return GetMatchPage(
        match,
        catalog or FakeCatalog(),
        library or FakeLibrary(),
        progression or FakeAppearance(),  # type: ignore[arg-type]  # фейк
        concurrency=concurrency,
        items_deadline_s=deadline_s,
        appearance_batch_size=batch,
        items_batch_size=items_batch,
    )


def player(n: int) -> MatchPlayer:
    return MatchPlayer(
        user_id=UUID(int=n),
        nickname=f"p{n}",
        slot=n,
        place=1,
        score=0,
        outcome=None,
        finish="completed",
    )


async def test_match_page_appearance_is_batched_by_12_and_deduplicated() -> None:
    players = [player(n) for n in range(1, 15)] + [player(3)]  # 14 разных + дубль
    outcome = replace(result(ITEM_A), players=players)
    progression = FakeAppearance()

    page = await match_page(FakeMatch(outcome=outcome), progression=progression)(USER, MATCH_ID)

    assert [len(c) for c in progression.calls] == [12, 2]
    assert page.blocks.appearance is not None
    assert [c.user_id for c in page.blocks.appearance.data or []] == [
        UUID(int=n) for n in range(1, 15)
    ]


async def test_match_page_appearance_follows_match_player_order_not_answer_order() -> None:
    """Источник отвечает в обратном порядке: порядок карточек - по игрокам матча."""
    outcome = replace(result(ITEM_A), players=[player(n) for n in (5, 2, 9, 1, 7)])
    progression = FakeAppearance(reverse=True)

    page = await match_page(FakeMatch(outcome=outcome), progression=progression, batch=2)(
        USER, MATCH_ID
    )

    assert page.blocks.appearance is not None
    got = [c.user_id for c in page.blocks.appearance.data or []]
    assert got == [UUID(int=n) for n in (5, 2, 9, 1, 7)]


async def test_match_page_appearance_failure_hides_only_its_block() -> None:
    outcome = replace(result(ITEM_A), players=[player(1)])
    progression = FakeAppearance(fail=DOWN)

    page = await match_page(FakeMatch(outcome=outcome), progression=progression)(USER, MATCH_ID)

    assert page.blocks.appearance is not None and not page.blocks.appearance.is_ok
    assert page.blocks.match.is_ok and page.blocks.items.is_ok and page.blocks.library.is_ok


# ----------------------------------------------------------------------------- хаб
async def test_online_user_gets_all_blocks_with_limits() -> None:
    match = FakeMatch()

    page = await online(match)(USER)

    assert page.status is OnlineStatus.OK
    assert page.blocks is not None
    assert page.blocks.open_rooms.is_ok and page.blocks.my_matches.is_ok
    cards = page.blocks.modes.data or []
    assert [m.key for m in cards] == ["film.duel.frame"]
    assert cards[0].online is ONLINE_MODE.online
    assert sorted(match.calls) == [
        ("matches", "user-token", "10"),
        ("pool", "user-token"),
        ("rooms", "user-token", "20"),
    ]


@pytest.mark.parametrize("viewer", [ANONYMOUS, GUEST], ids=["anonymous", "guest"])
async def test_online_guest_and_anonymous_are_auth_required_without_calls(viewer: Viewer) -> None:
    match, game = FakeMatch(), FakeGame()

    page = await online(match, game)(viewer)

    assert page.status is OnlineStatus.AUTH_REQUIRED
    assert page.blocks is None
    assert match.calls == [] and game.calls == 0


async def test_online_unverified_guest_token_is_auth_required() -> None:
    """JWKS недоступен: гостя распознаёт match (403) — экран регистрации."""
    page = await online(FakeMatch(rooms=GUEST_REJECTED, matches=GUEST_REJECTED))(UNVERIFIED)
    assert page.status is OnlineStatus.AUTH_REQUIRED


async def test_online_match_401_is_session_expired() -> None:
    """match не принял токен (отозван после выдачи) — фронт обновит сессию и повторит."""
    revoked = UpstreamRejectedError("match", 401, "auth.session_revoked")
    page = await online(FakeMatch(rooms=revoked))(USER)
    assert page.status is OnlineStatus.SESSION_EXPIRED
    assert page.blocks is None


async def test_online_locally_rejected_token_is_session_expired_without_calls() -> None:
    match, game = FakeMatch(), FakeGame()

    page = await online(match, game)(Viewer(token_rejected=True))

    assert page.status is OnlineStatus.SESSION_EXPIRED
    assert match.calls == [] and game.calls == 0


async def test_online_unverified_user_token_is_accepted_by_match() -> None:
    page = await online(FakeMatch())(UNVERIFIED)
    assert page.status is OnlineStatus.OK


async def test_online_blocks_degrade_independently() -> None:
    other_4xx = UpstreamRejectedError("match", 422, "core.invalid_input")
    page = await online(FakeMatch(rooms=DOWN, matches=other_4xx), FakeGame(online=DOWN))(USER)

    assert page.status is OnlineStatus.OK
    assert page.blocks is not None
    assert page.blocks.open_rooms.status is BlockStatus.UNAVAILABLE
    assert page.blocks.my_matches.status is BlockStatus.UNAVAILABLE
    assert page.blocks.modes.status is BlockStatus.UNAVAILABLE


# ----------------------------------------------------------------------------- итог матча
async def test_match_page_joins_items_and_flags_without_duplicates() -> None:
    library = FakeLibrary(flags={ITEM_A: ["system:watched"]})
    match = FakeMatch(outcome=result(ITEM_A, ITEM_B, ITEM_A))

    page = await match_page(match, library=library)(USER, MATCH_ID)

    assert page.blocks.match.is_ok
    assert [c.media_item_id for c in page.blocks.items.data or []] == [ITEM_A, ITEM_B]
    assert page.blocks.library.data == [
        ItemShelves(ITEM_A, ["system:watched"]),
        ItemShelves(ITEM_B, []),
    ]
    assert library.calls == [[ITEM_A, ITEM_B]]  # одна пачка без дублей


async def test_match_page_anonymous_401_guest_403_without_match_call() -> None:
    match = FakeMatch()
    with pytest.raises(UnauthenticatedError):
        await match_page(match)(ANONYMOUS, MATCH_ID)
    with pytest.raises(ForbiddenError) as exc:
        await match_page(match)(GUEST, MATCH_ID)
    assert exc.value.code == "auth.guest_forbidden"
    assert match.calls == []


async def test_match_page_not_found_keeps_match_code() -> None:
    missing = UpstreamRejectedError("match", 404, "match.match_not_found")
    with pytest.raises(NotFoundError) as exc:
        await match_page(FakeMatch(outcome=missing))(USER, MATCH_ID)
    assert exc.value.code == "match.match_not_found"


async def test_match_page_match_down_gives_all_blocks_unavailable() -> None:
    library = FakeLibrary()
    page = await match_page(FakeMatch(outcome=DOWN), library=library)(USER, MATCH_ID)

    assert page.blocks.match.status is BlockStatus.UNAVAILABLE
    assert page.blocks.items.status is BlockStatus.UNAVAILABLE
    assert page.blocks.library.status is BlockStatus.UNAVAILABLE
    assert library.calls == []


async def test_match_page_one_call_per_source_for_eight_rounds() -> None:
    ids = [UUID(int=i + 1) for i in range(8)]
    catalog, library = FakeCatalog(), FakeLibrary()

    page = await match_page(FakeMatch(outcome=result(*ids)), catalog, library)(USER, MATCH_ID)

    assert [c.media_item_id for c in page.blocks.items.data or []] == ids
    assert len(catalog.batches) == len(library.calls) == 1  # а не 8 + 8


async def test_match_page_splits_into_chunks_by_batch_size() -> None:
    ids = [UUID(int=i + 1) for i in range(7)]
    catalog, library = FakeCatalog(), FakeLibrary()

    page = await match_page(FakeMatch(outcome=result(*ids)), catalog, library, items_batch=3)(
        USER, MATCH_ID
    )

    assert sorted(len(b) for b in catalog.batches) == [1, 3, 3]
    assert sorted(len(b) for b in library.calls) == [1, 3, 3]
    # порядок раундов сохранён, хотя источник отвечает вразнобой
    assert [c.media_item_id for c in page.blocks.items.data or []] == ids
    assert [f.media_item_id for f in page.blocks.library.data or []] == ids


async def test_match_page_catalog_and_library_degrade_independently() -> None:
    catalog = FakeCatalog(items={ITEM_B: UpstreamUnavailableError("catalog", "timeout")})
    page = await match_page(FakeMatch(), catalog)(USER, MATCH_ID)
    assert page.blocks.items.status is BlockStatus.UNAVAILABLE
    assert page.blocks.library.is_ok

    library = FakeLibrary(flags={ITEM_A: UpstreamUnavailableError("library", "http_503")})
    page = await match_page(FakeMatch(), library=library)(USER, MATCH_ID)
    assert page.blocks.items.is_ok
    assert page.blocks.library.status is BlockStatus.UNAVAILABLE


async def test_match_page_missing_item_and_unknown_flag_are_tolerated() -> None:
    catalog = FakeCatalog(items={ITEM_B: UpstreamRejectedError("catalog", 404, "nf")})
    library = FakeLibrary(flags={ITEM_B: UpstreamRejectedError("library", 404, "nf")})

    page = await match_page(FakeMatch(), catalog, library)(USER, MATCH_ID)

    assert [c.media_item_id for c in page.blocks.items.data or []] == [ITEM_A]
    assert page.blocks.library.data == [ItemShelves(ITEM_A, []), ItemShelves(ITEM_B, [])]


@pytest.mark.parametrize("status", [400, 410, 418])
async def test_match_page_undescribed_4xx_is_unavailable_not_404(status: int) -> None:
    library = FakeLibrary()
    odd = UpstreamRejectedError("match", status, "match.something")

    page = await match_page(FakeMatch(outcome=odd), library=library)(USER, MATCH_ID)

    assert page.blocks.match.status is BlockStatus.UNAVAILABLE
    assert page.blocks.items.status is BlockStatus.UNAVAILABLE
    assert page.blocks.library.status is BlockStatus.UNAVAILABLE
    assert library.calls == []


@dataclass
class InFlight:
    """Счётчик одновременных вызовов catalog и library (общий Limiter запроса)."""

    now: int = 0
    peak: int = 0

    async def hold(self) -> None:
        self.now += 1
        self.peak = max(self.peak, self.now)
        await asyncio.sleep(0.005)
        self.now -= 1


@dataclass
class CountingCatalog(FakeCatalog):
    flight: InFlight = field(default_factory=InFlight)

    async def get_items(self, item_ids: Sequence[UUID]) -> list[Item]:
        await self.flight.hold()
        return [item(i) for i in item_ids]


@dataclass
class CountingLibrary(FakeLibrary):
    flight: InFlight = field(default_factory=InFlight)

    async def items_shelves(self, token: str, media_item_ids: Sequence[UUID]) -> list[ItemShelves]:
        await self.flight.hold()
        return [ItemShelves(i, []) for i in media_item_ids]


async def test_match_page_limiter_holds_concurrency_threshold() -> None:
    ids = [UUID(int=i + 1) for i in range(25)]
    flight = InFlight()
    catalog, library = CountingCatalog(flight=flight), CountingLibrary(flight=flight)

    page = await match_page(
        FakeMatch(outcome=result(*ids)), catalog, library, concurrency=3, items_batch=5
    )(USER, MATCH_ID)

    assert len(page.blocks.items.data or []) == len(page.blocks.library.data or []) == 25
    assert flight.peak == 3  # 2 источника x 5 пачек по 5, но не больше 3 одновременно


@dataclass
class SlowCatalog(FakeCatalog):
    async def get_items(self, item_ids: Sequence[UUID]) -> list[Item]:
        await asyncio.sleep(10)
        raise AssertionError("должен был сработать дедлайн")


async def test_match_page_items_deadline_hides_slow_block_only() -> None:
    started = time.monotonic()

    page = await match_page(FakeMatch(), SlowCatalog(), deadline_s=0.05)(USER, MATCH_ID)

    assert time.monotonic() - started < 1
    assert page.blocks.match.is_ok
    assert page.blocks.items.status is BlockStatus.UNAVAILABLE
    assert page.blocks.library.is_ok


# ----------------------------------------------------------------------------- 4b
USER_ID = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000e01")
SILVER = TierCard("silver_2", "silver", 2, "rating.tier.silver")
SEASON = SeasonCard("film", 4, AT, datetime(2026, 12, 17, 12, 0, tzinfo=UTC))
LADDER = LadderCard(
    key="film", domain="film", formats=["classic", "lite"], season=SEASON, tiers=[SILVER]
)
LADDER_MUSIC = LadderCard(key="music", domain="music", formats=["classic"], season=None, tiers=[])
RATING = RatingCard(
    ladder="film",
    rating=1234,
    tier=SILVER,
    next_tier_at=1500,
    calibrating=False,
    calibration_left=0,
    matches_played=15,
    place=42,
)
CALIBRATING = RatingCard(
    ladder="music", rating=None, tier=None, calibrating=True, calibration_left=7, matches_played=3
)
ENTRY = WeeklyEntryCard(1, USER_ID, "neo", 55)
WEEKLY = WeeklyTopCard("film", date(2026, 10, 19), [ENTRY, ENTRY, ENTRY], me=ENTRY)
BOARD_ROW = LeaderboardEntryCard(1, USER_ID, "neo", 1800, SILVER, 40)
BOARD = LeaderboardCard("film", [BOARD_ROW], next_cursor="c2")


@dataclass
class FakeRating:
    """Фейк `RatingClient`: значение или исключение на каждый метод, журнал вызовов."""

    ladder_list: list[LadderCard] | Exception = field(
        default_factory=lambda: [LADDER, LADDER_MUSIC]
    )
    mine: list[RatingCard] | Exception = field(default_factory=lambda: [RATING, CALIBRATING])
    board: LeaderboardCard | Exception = field(default_factory=lambda: BOARD)
    board_around: LeaderboardCard | Exception | None = None
    top: WeeklyTopCard | Exception = field(default_factory=lambda: WEEKLY)
    calls: list[tuple[object, ...]] = field(default_factory=list)

    async def ladders(self) -> list[LadderCard]:
        self.calls.append(("ladders",))
        if isinstance(self.ladder_list, Exception):
            raise self.ladder_list
        return self.ladder_list

    async def my_ratings(self, token: str) -> list[RatingCard]:
        self.calls.append(("mine", token))
        if isinstance(self.mine, Exception):
            raise self.mine
        return self.mine

    async def leaderboard(
        self, ladder: str, *, token: str | None, cursor: str | None, around_me: bool
    ) -> LeaderboardCard:
        self.calls.append(("board", ladder, token, cursor, around_me))
        value = self.board_around if around_me and self.board_around is not None else self.board
        if isinstance(value, Exception):
            raise value
        return value

    async def weekly(self, ladder: str, *, token: str | None) -> WeeklyTopCard:
        self.calls.append(("weekly", ladder, token))
        if isinstance(self.top, Exception):
            raise self.top
        return WeeklyTopCard(ladder, self.top.week_start, self.top.items, self.top.me)


RATING_DOWN = UpstreamUnavailableError("rating", "timeout")
NOT_RANKED = UpstreamRejectedError("rating", 404, "rating.not_ranked")
NO_LADDER = UpstreamRejectedError("rating", 404, "rating.ladder_not_found")


def leaderboards(rating: FakeRating) -> GetLeaderboardsPage:
    return GetLeaderboardsPage(rating, concurrency=10)


# ----------------------------------------------------------------------------- хаб онлайна
async def test_hub_fills_4b_blocks() -> None:
    match, rating = FakeMatch(), FakeRating()

    page = await online(match, rating=rating)(USER)

    blocks = page.blocks
    assert blocks is not None
    assert [(q.ladder, q.format, q.searching) for q in blocks.queues.data or []] == [
        ("film", "classic", 3)
    ]
    assert blocks.ratings.data == [RATING, CALIBRATING]
    assert blocks.seasons.data == [SEASON]  # ladder без сезона в блок не попадает
    weekly = blocks.weekly.data or []
    assert [w.ladder for w in weekly] == ["film", "music"]
    assert all(len(w.items) == 2 and w.me is not None for w in weekly)  # weekly_limit
    assert ("mine", "user-token") in rating.calls
    assert ("weekly", "film", "user-token") in rating.calls


async def test_hub_rating_down_hides_its_blocks_only() -> None:
    rating = FakeRating(ladder_list=RATING_DOWN, mine=RATING_DOWN)

    page = await online(FakeMatch(), rating=rating)(USER)

    blocks = page.blocks
    assert blocks is not None
    for hidden in (blocks.ratings, blocks.seasons, blocks.weekly):
        assert hidden.status is BlockStatus.UNAVAILABLE
    assert blocks.queues.status is BlockStatus.OK
    assert blocks.open_rooms.status is BlockStatus.OK


async def test_hub_weekly_failure_of_one_ladder_hides_weekly_block() -> None:
    page = await online(FakeMatch(), rating=FakeRating(top=RATING_DOWN))(USER)

    assert page.blocks is not None
    assert page.blocks.weekly.status is BlockStatus.UNAVAILABLE
    assert page.blocks.seasons.status is BlockStatus.OK


async def test_hub_match_pool_down_hides_queues_only() -> None:
    page = await online(FakeMatch(pool=UpstreamUnavailableError("match", "timeout")))(USER)

    assert page.blocks is not None
    assert page.blocks.queues.status is BlockStatus.UNAVAILABLE
    assert page.blocks.ratings.status is BlockStatus.OK


async def test_hub_rating_401_is_session_expired() -> None:
    expired = UpstreamRejectedError("rating", 401, "auth.unauthenticated")

    page = await online(FakeMatch(), rating=FakeRating(mine=expired))(USER)

    assert page.status is OnlineStatus.SESSION_EXPIRED


async def test_hub_rating_guest_403_is_auth_required() -> None:
    guest = UpstreamRejectedError("rating", 403, "auth.guest_forbidden")

    page = await online(FakeMatch(), rating=FakeRating(mine=guest))(USER)

    assert page.status is OnlineStatus.AUTH_REQUIRED


async def test_hub_anonymous_does_not_call_rating() -> None:
    rating, match = FakeRating(), FakeMatch()

    await online(match, rating=rating)(ANONYMOUS)

    assert rating.calls == [] and match.calls == []


# ----------------------------------------------------------------------------- лидерборды
async def test_leaderboards_default_ladder_is_first_and_page_is_public() -> None:
    rating = FakeRating()

    page = await leaderboards(rating)(ANONYMOUS, None, None, around_me=False)

    assert page.personal is False
    assert page.blocks.ladders.data == [LADDER, LADDER_MUSIC]
    assert page.blocks.leaderboard.data == BOARD
    assert (page.blocks.weekly.data or None) is not None
    assert ("board", "film", None, None, False) in rating.calls
    assert ("weekly", "film", None) in rating.calls


async def test_leaderboards_explicit_ladder_and_cursor() -> None:
    rating = FakeRating()

    await leaderboards(rating)(ANONYMOUS, "music", "c1", around_me=False)

    assert ("board", "music", None, "c1", False) in rating.calls
    assert ("weekly", "music", None) in rating.calls


async def test_leaderboards_guest_and_rejected_token_are_public() -> None:
    rating = FakeRating()

    for viewer in (GUEST, Viewer(token_rejected=True)):
        page = await leaderboards(rating)(viewer, "film", None, around_me=False)
        assert page.personal is False
    assert all(c[2] is None for c in rating.calls if c[0] in {"board", "weekly"})


async def test_leaderboards_user_gets_personal_page() -> None:
    rating = FakeRating()

    page = await leaderboards(rating)(USER, "film", None, around_me=True)

    assert page.personal is True
    assert ("board", "film", "user-token", None, True) in rating.calls
    assert ("weekly", "film", "user-token") in rating.calls


async def test_leaderboards_user_without_around_is_still_public_leaderboard() -> None:
    """Без `around=me` токен не нужен: страница общая и кэшируется."""
    rating = FakeRating()

    page = await leaderboards(rating)(USER, "film", None, around_me=False)

    assert page.personal is False
    assert ("board", "film", None, None, False) in rating.calls


@pytest.mark.parametrize("viewer", [ANONYMOUS, GUEST], ids=["anonymous", "guest"])
async def test_leaderboards_around_me_needs_a_user(viewer: Viewer) -> None:
    rating = FakeRating()

    with pytest.raises(UnauthenticatedError):
        await leaderboards(rating)(viewer, "film", None, around_me=True)
    assert rating.calls == []


async def test_leaderboards_around_me_in_calibration_falls_back_to_plain_page() -> None:
    rating = FakeRating(board_around=NOT_RANKED)

    page = await leaderboards(rating)(USER, "film", None, around_me=True)

    assert page.blocks.leaderboard.data == BOARD
    assert ("board", "film", "user-token", None, False) in rating.calls


async def test_leaderboards_unknown_ladder_is_404() -> None:
    with pytest.raises(NotFoundError) as exc:
        await leaderboards(FakeRating())(ANONYMOUS, "nope", None, around_me=False)
    assert exc.value.code == "rating.ladder_not_found"

    # Список ladder-ов недоступен — решает сам rating.
    with pytest.raises(NotFoundError):
        await leaderboards(FakeRating(board=NO_LADDER, ladder_list=RATING_DOWN))(
            ANONYMOUS, "nope", None, around_me=False
        )


async def test_leaderboards_bad_cursor_is_422() -> None:
    bad = UpstreamRejectedError("rating", 422, "core.invalid_input")

    with pytest.raises(InvalidInputError):
        await leaderboards(FakeRating(board=bad))(ANONYMOUS, "film", "zzz", around_me=False)


async def test_leaderboards_rating_down_degrades_blocks() -> None:
    page = await leaderboards(
        FakeRating(ladder_list=RATING_DOWN, board=RATING_DOWN, top=RATING_DOWN)
    )(ANONYMOUS, "film", None, around_me=False)

    blocks = page.blocks
    for block in (blocks.ladders, blocks.leaderboard, blocks.weekly):
        assert block.status is BlockStatus.UNAVAILABLE


async def test_leaderboards_without_ladders_and_key_blocks_hide() -> None:
    rating = FakeRating(ladder_list=RATING_DOWN)

    page = await leaderboards(rating)(ANONYMOUS, None, None, around_me=False)

    assert page.blocks.leaderboard.status is BlockStatus.UNAVAILABLE
    assert page.blocks.weekly.status is BlockStatus.UNAVAILABLE
    assert [c[0] for c in rating.calls] == ["ladders"]


# ----------------------------------------------------------------------------- комнаты
HOST = UUID(int=101)
GUEST_PLAYER = UUID(int=102)
SETTINGS = RoomSettings(
    rounds=7,
    level_window_s=15,
    answer_format="classic",
    clue_type="frame_progressive",
    players_max=2,
    visibility="public",
)


def open_room(code: str, host: UUID, name: str | None = None, nick: str = "Host") -> OpenRoom:
    return OpenRoom(
        code=code,
        mode=MODE,
        host_id=host,
        host_nickname=nick,
        players=1,
        players_max=2,
        settings=replace(SETTINGS, name=name),
        created_at=AT,
    )


@dataclass
class FakeLevels:
    """Уровни игроков (progression, internal): пачки, сбои, журнал вызовов."""

    known: dict[UUID, int] = field(default_factory=dict)
    fail: Exception | None = None
    calls: list[list[UUID]] = field(default_factory=list)

    async def levels(self, user_ids: Sequence[UUID]) -> dict[UUID, PlayerLevel]:
        self.calls.append(list(user_ids))
        if self.fail is not None:
            raise self.fail
        return {u: PlayerLevel(u, self.known[u], 0) for u in user_ids if u in self.known}


@dataclass
class FakeSocialCount:
    """social: только публичный счётчик онлайна."""

    value: int | Exception = 128
    calls: int = 0

    async def friends(self, token: str) -> list[object]:
        return []

    async def incoming_requests(self, token: str) -> int:
        return 0

    async def online_count(self) -> int:
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def hub(
    match: FakeMatch,
    *,
    levels: FakeLevels | None = None,
    social: FakeSocialCount | None = None,
    batch: int = 50,
) -> GetOnlinePage:
    return GetOnlinePage(
        match,
        FakeGame(),
        FakeRating(),
        concurrency=10,
        rooms_limit=20,
        matches_limit=10,
        weekly_limit=2,
        social=social,  # type: ignore[arg-type]  # фейк с одним методом
        progression=levels,  # type: ignore[arg-type]  # фейк с одним методом
        room_name_template="Комната {host}",
        levels_batch_size=batch,
    )


async def test_hub_online_count_is_a_block_and_fails_alone() -> None:
    page = await hub(FakeMatch(), social=FakeSocialCount(128))(USER)
    assert page.blocks is not None and page.blocks.online_count is not None
    assert page.blocks.online_count.data is not None
    assert page.blocks.online_count.data.count == 128

    down = await hub(FakeMatch(), social=FakeSocialCount(UpstreamUnavailableError("social", "x")))(
        USER
    )
    assert down.blocks is not None and down.blocks.online_count is not None
    assert down.blocks.online_count.status is BlockStatus.UNAVAILABLE
    assert down.blocks.queues.status is BlockStatus.OK  # остальной хаб цел


async def test_hub_without_social_has_no_online_count() -> None:
    page = await hub(FakeMatch())(USER)
    assert page.blocks is not None and page.blocks.online_count is None


async def test_open_rooms_get_title_and_host_level_deduplicated() -> None:
    other = UUID(int=103)
    rooms = OpenRooms(
        [
            open_room("AAAAAAA2", HOST, name="Вечер кино"),
            open_room("AAAAAAA3", other, nick="Маша"),
            open_room("AAAAAAA4", HOST),
        ]
    )
    levels = FakeLevels(known={HOST: 12})

    page = await hub(FakeMatch(rooms=rooms), levels=levels)(USER)

    assert levels.calls == [[HOST, other]]
    assert page.blocks is not None and page.blocks.open_rooms.data is not None
    got = [(r.title, r.host_level) for r in page.blocks.open_rooms.data.items]
    assert got == [("Вечер кино", 12), ("Комната Маша", None), ("Комната Host", 12)]
    assert page.blocks.open_rooms.data.items[0].settings.name == "Вечер кино"


async def test_open_rooms_survive_progression_failure_without_levels() -> None:
    rooms = OpenRooms([open_room("AAAAAAA2", HOST)])
    levels = FakeLevels(fail=UpstreamUnavailableError("progression", "service_token_unavailable"))

    page = await hub(FakeMatch(rooms=rooms), levels=levels)(USER)

    assert page.blocks is not None and page.blocks.open_rooms.data is not None
    [card] = page.blocks.open_rooms.data.items
    assert (card.title, card.host_level) == ("Комната Host", None)


def lobby() -> RoomLobby:
    return RoomLobby(
        code="AAAAAAA2",
        mode=MODE,
        host_id=HOST,
        title="",
        settings=SETTINGS,
        players=[
            RoomPlayer(user_id=HOST, nickname="Host", is_host=True, ready=True, presence="online"),
            RoomPlayer(
                user_id=GUEST_PLAYER, nickname="Anna", is_host=False, ready=False, presence="online"
            ),
        ],
        players_min=2,
        phase="lobby",
    )


def room_page(match: FakeMatch, levels: FakeLevels, batch: int = 50) -> GetRoomPage:
    return GetRoomPage(
        match,
        levels,  # type: ignore[arg-type]  # фейк с одним методом
        concurrency=10,
        room_name_template="Комната {host}",
        levels_batch_size=batch,
    )


async def test_room_page_adds_levels_and_default_title() -> None:
    levels = FakeLevels(known={HOST: 7})
    match = FakeMatch(lobby=lobby())

    page = await room_page(match, levels)(USER, "AAAAAAA2")

    assert match.calls == [("room", "user-token", "AAAAAAA2")]
    room = page.blocks.room.data
    assert room is not None
    assert room.title == "Комната Host"
    assert [(p.nickname, p.level) for p in room.players] == [("Host", 7), ("Anna", None)]


async def test_room_page_keeps_host_given_name_and_survives_progression_failure() -> None:
    named = replace(lobby(), settings=replace(SETTINGS, name="Тихий вечер"))
    levels = FakeLevels(fail=UpstreamUnavailableError("progression", "timeout"))

    page = await room_page(FakeMatch(lobby=named), levels)(USER, "AAAAAAA2")

    room = page.blocks.room.data
    assert room is not None
    assert room.title == "Тихий вечер"
    assert [p.level for p in room.players] == [None, None]


async def test_room_page_levels_go_in_batches() -> None:
    levels = FakeLevels(known={HOST: 3, GUEST_PLAYER: 4})
    await room_page(FakeMatch(lobby=lobby()), levels, batch=1)(USER, "AAAAAAA2")
    assert levels.calls == [[HOST], [GUEST_PLAYER]]


async def test_room_page_guest_anonymous_and_match_errors() -> None:
    levels = FakeLevels()
    match = FakeMatch(lobby=lobby())
    with pytest.raises(UnauthenticatedError):
        await room_page(match, levels)(ANONYMOUS, "AAAAAAA2")
    with pytest.raises(ForbiddenError):
        await room_page(match, levels)(GUEST, "AAAAAAA2")
    assert match.calls == [] and levels.calls == []

    missing = FakeMatch(lobby=UpstreamRejectedError("match", 404, "match.room_not_found"))
    with pytest.raises(NotFoundError):
        await room_page(missing, levels)(USER, "AAAAAAA2")
    down = FakeMatch(lobby=DOWN)
    page = await room_page(down, levels)(USER, "AAAAAAA2")
    assert page.blocks.room.status is BlockStatus.UNAVAILABLE
    assert levels.calls == []
