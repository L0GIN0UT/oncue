"""Экраны онлайна: хаб и итог матча — гость, деградация блоков, отказы match (этап 4a, §8)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    ANONYMOUS,
    CollectionMode,
    DailyStatus,
    Domain,
    Item,
    ItemShelves,
    MatchMode,
    MatchResult,
    MatchRound,
    Mode,
    MyMatches,
    OnlineStatus,
    OpenRooms,
    PersonalRecord,
    SessionResult,
    Shelf,
    ShelfItems,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.application.queries import GetMatchPage, GetOnlinePage
from bff_web_service.domain import BlockStatus

from gv_core.errors import ForbiddenError, NotFoundError, UnauthenticatedError

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
    calls: list[tuple[str, ...]] = field(default_factory=list)

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

    async def get_item(self, item_id: UUID) -> Item:
        value = self.items.get(item_id, item(item_id))
        if isinstance(value, Exception):
            raise value
        return value


@dataclass
class FakeLibrary:
    flags: dict[UUID, list[str] | Exception] = field(default_factory=dict)
    calls: list[UUID] = field(default_factory=list)

    async def item_shelves(self, token: str, media_item_id: UUID) -> ItemShelves:
        self.calls.append(media_item_id)
        value = self.flags.get(media_item_id, [])
        if isinstance(value, Exception):
            raise value
        return ItemShelves(media_item_id, value)

    async def shelves(self, token: str) -> list[Shelf]:
        raise AssertionError("итог матча не читает полки")

    async def shelf_items(
        self, token: str, shelf_ref: str, *, cursor: str | None, limit: int
    ) -> ShelfItems:
        raise AssertionError("итог матча не читает полку")


def online(match: FakeMatch, game: FakeGame | None = None) -> GetOnlinePage:
    return GetOnlinePage(
        match, game or FakeGame(), concurrency=10, rooms_limit=20, matches_limit=10
    )


def match_page(
    match: FakeMatch,
    catalog: FakeCatalog | None = None,
    library: FakeLibrary | None = None,
    *,
    concurrency: int = 10,
    deadline_s: float = 5.0,
) -> GetMatchPage:
    return GetMatchPage(
        match,
        catalog or FakeCatalog(),
        library or FakeLibrary(),
        concurrency=concurrency,
        items_deadline_s=deadline_s,
    )


# ----------------------------------------------------------------------------- хаб
async def test_online_user_gets_all_blocks_with_limits() -> None:
    match = FakeMatch()

    page = await online(match)(USER)

    assert page.status is OnlineStatus.OK
    assert page.blocks is not None
    assert page.blocks.open_rooms.is_ok and page.blocks.my_matches.is_ok
    assert [m.key for m in page.blocks.modes.data or []] == ["film.duel.frame"]
    assert sorted(match.calls) == [("matches", "user-token", "10"), ("rooms", "user-token", "20")]


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
    assert library.calls == [ITEM_A, ITEM_B]


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

    async def get_item(self, item_id: UUID) -> Item:
        await self.flight.hold()
        return item(item_id)


@dataclass
class CountingLibrary(FakeLibrary):
    flight: InFlight = field(default_factory=InFlight)

    async def item_shelves(self, token: str, media_item_id: UUID) -> ItemShelves:
        await self.flight.hold()
        return ItemShelves(media_item_id, [])


async def test_match_page_limiter_holds_concurrency_threshold() -> None:
    ids = [UUID(int=i + 1) for i in range(25)]
    flight = InFlight()
    catalog, library = CountingCatalog(flight=flight), CountingLibrary(flight=flight)

    page = await match_page(FakeMatch(outcome=result(*ids)), catalog, library, concurrency=3)(
        USER, MATCH_ID
    )

    assert len(page.blocks.items.data or []) == len(page.blocks.library.data or []) == 25
    assert flight.peak == 3  # 50 вызовов, но не больше 3 одновременно (match уже ответил)


@dataclass
class SlowCatalog(FakeCatalog):
    async def get_item(self, item_id: UUID) -> Item:
        await asyncio.sleep(10)
        raise AssertionError("должен был сработать дедлайн")


async def test_match_page_items_deadline_hides_slow_block_only() -> None:
    started = time.monotonic()

    page = await match_page(FakeMatch(), SlowCatalog(), deadline_s=0.05)(USER, MATCH_ID)

    assert time.monotonic() - started < 1
    assert page.blocks.match.is_ok
    assert page.blocks.items.status is BlockStatus.UNAVAILABLE
    assert page.blocks.library.is_ok
