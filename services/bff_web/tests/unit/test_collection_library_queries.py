"""Экраны сборников и медиатеки: блоки, деградация и отказы источников (этап 3b, §9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    ANONYMOUS,
    CatalogQuery,
    CollectionAuthor,
    CollectionCard,
    CollectionDetail,
    CollectionList,
    CollectionMe,
    CollectionMode,
    DailyStatus,
    Domain,
    Mode,
    PersonalRecord,
    SessionResult,
    Shelf,
    ShelfItem,
    ShelfItems,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.application.queries import (
    GetCollectionPage,
    GetCollectionsPage,
    GetLibraryPage,
    GetLibraryShelfPage,
)
from bff_web_service.domain import BlockStatus

from gv_core.errors import (
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
CID = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000c01")
ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a1")
USER = Viewer(token="user-token", kind=SubjectKind.USER, verified=True)
GUEST = Viewer(token="guest-token", kind=SubjectKind.GUEST, verified=True)
UNVERIFIED = Viewer(token="some-token")
DOWN = UpstreamUnavailableError("collections", "timeout")
ME = CollectionMe(liked=True, favorite=False, completed=True)


def card(title: str = "Кино · кадры") -> CollectionCard:
    return CollectionCard(
        collection_id=CID,
        domain="film",
        kind="ugc",
        title=title,
        tags=["cinema"],
        author=CollectionAuthor("user", ITEM, "neo"),
        default_mode_key="film.collection.frame",
        items_count=8,
        likes_count=3,
        plays_count=10,
        published_at=AT,
    )


def detail(me: CollectionMe | None = ME) -> CollectionDetail:
    return CollectionDetail(
        collection_id=CID,
        domain="film",
        kind="ugc",
        title="Кино · кадры",
        tags=[],
        author=CollectionAuthor("user", ITEM, "neo"),
        default_mode_key="film.collection.frame",
        items_count=8,
        likes_count=3,
        plays_count=10,
        published_at=AT,
        visibility="public",
        status="published",
        version=2,
        items_visible=False,
        me=me,
    )


COLLECTION_MODE = CollectionMode(
    mode_key="film.collection.frame",
    title_key="modes.film.collection.frame.title",
    clue_type="frame_progressive",
    allow_guest=True,
    playable=7,
    total=8,
    available=True,
    round_options=[5, "all"],
)


@dataclass
class FakeGame:
    """game для страницы сборника: нужны только режимы сборника."""

    collection: list[CollectionMode] | Exception = field(default_factory=list)
    collection_calls: list[UUID] = field(default_factory=list)

    async def list_modes(self) -> list[Mode]:
        raise AssertionError("страница сборника не читает /v1/modes")

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        raise AssertionError("страница сборника не читает дейлики")

    async def records(self, token: str) -> list[PersonalRecord]:
        raise AssertionError("страница сборника не читает рекорды")

    async def session_result(self, token: str, session_id: UUID) -> SessionResult:
        raise AssertionError("страница сборника не читает итог")

    async def collection_modes(self, collection_id: UUID) -> list[CollectionMode]:
        self.collection_calls.append(collection_id)
        if isinstance(self.collection, Exception):
            raise self.collection
        return self.collection


@dataclass
class FakeCollections:
    pages: dict[str, CollectionList | Exception] = field(default_factory=dict)
    answer: CollectionDetail | Exception = field(default_factory=detail)
    queries: list[CatalogQuery] = field(default_factory=list)
    detail_tokens: list[str | None] = field(default_factory=list)

    async def catalog(self, query: CatalogQuery) -> CollectionList:
        self.queries.append(query)
        value = self.pages.get(query.sort, CollectionList([card(query.sort)], f"next-{query.sort}"))
        if isinstance(value, Exception):
            raise value
        return value

    async def detail(self, token: str | None, collection_id: UUID) -> CollectionDetail:
        self.detail_tokens.append(token)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def shelf(ref: str = "system:watch_later") -> Shelf:
    return Shelf(ref=ref, kind="system", key="watch_later", domains=["film"], items_count=1)


@dataclass
class FakeLibrary:
    shelves_answer: list[Shelf] | Exception = field(default_factory=lambda: [shelf()])
    items_answer: ShelfItems | Exception = field(
        default_factory=lambda: ShelfItems(
            shelf(),
            [
                ShelfItem(
                    media_item_id=ITEM,
                    domain="film",
                    kind="movie",
                    title="Брат",
                    status="available",
                    source="reveal",
                    added_at=AT,
                )
            ],
            "c2",
        )
    )
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def shelves(self, token: str) -> list[Shelf]:
        self.calls.append((token,))
        if isinstance(self.shelves_answer, Exception):
            raise self.shelves_answer
        return self.shelves_answer

    async def shelf_items(
        self, token: str, shelf_ref: str, *, cursor: str | None, limit: int
    ) -> ShelfItems:
        self.calls.append((token, shelf_ref, str(cursor), str(limit)))
        if isinstance(self.items_answer, Exception):
            raise self.items_answer
        return self.items_answer


def catalog_page(collections: FakeCollections) -> GetCollectionsPage:
    return GetCollectionsPage(collections, concurrency=10, showcase_limit=12, results_limit=20)


# ----------------------------------------------------------------------------- каталог
async def test_catalog_without_q_has_three_showcases_with_shared_filters() -> None:
    collections = FakeCollections()
    page = await catalog_page(collections)(CatalogQuery(domain="film", curated=True, sort="likes"))

    assert page.cacheable
    assert page.blocks.results is None
    for name in ("featured", "popular", "new"):
        block = getattr(page.blocks, name)
        assert block is not None and block.data is not None
        assert block.data.items[0].title == name
        assert block.data.next_cursor == f"next-{name}"
    assert sorted(q.sort for q in collections.queries) == ["featured", "new", "popular"]
    assert {(q.domain, q.curated, q.limit, q.q) for q in collections.queries} == {
        ("film", True, 12, None)
    }


async def test_catalog_showcase_degrades_alone() -> None:
    collections = FakeCollections(pages={"featured": DOWN})
    page = await catalog_page(collections)(CatalogQuery())
    assert page.blocks.featured is not None
    assert page.blocks.featured.status is BlockStatus.UNAVAILABLE
    assert page.blocks.popular is not None and page.blocks.popular.is_ok


async def test_catalog_search_is_results_only_and_not_cacheable() -> None:
    collections = FakeCollections()
    page = await catalog_page(collections)(CatalogQuery(q="кадры", sort="new"))
    assert not page.cacheable
    assert page.blocks.featured is None and page.blocks.popular is None
    assert page.blocks.results is not None and page.blocks.results.is_ok
    assert [(q.q, q.sort, q.limit) for q in collections.queries] == [("кадры", "new", 20)]


async def test_catalog_cursor_continues_one_sort_and_stays_cacheable() -> None:
    collections = FakeCollections()
    page = await catalog_page(collections)(CatalogQuery(sort="new", cursor="abc"))
    assert page.cacheable
    assert page.blocks.results is not None and page.blocks.results.is_ok
    assert [(q.sort, q.cursor) for q in collections.queries] == [("new", "abc")]


async def test_catalog_search_down_is_unavailable_block() -> None:
    page = await catalog_page(FakeCollections(pages={"popular": DOWN}))(CatalogQuery(q="x"))
    assert page.blocks.results is not None
    assert page.blocks.results.status is BlockStatus.UNAVAILABLE


async def test_catalog_rejected_cursor_is_input_error() -> None:
    rejected = UpstreamRejectedError("collections", 422, "collections.cursor_invalid")
    with pytest.raises(InvalidInputError) as caught:
        await catalog_page(FakeCollections(pages={"new": rejected}))(
            CatalogQuery(sort="new", cursor="zzz")
        )
    assert caught.value.code == "collections.cursor_invalid"


# ----------------------------------------------------------------------------- сборник
async def test_collection_page_joins_card_modes_and_me_for_user() -> None:
    collections, game = FakeCollections(), FakeGame(collection=[COLLECTION_MODE])
    page = await GetCollectionPage(collections, game, concurrency=10)(USER, CID)

    assert page.personal
    assert page.blocks.collection.data is not None
    assert page.blocks.collection.data.version == 2
    assert page.blocks.modes.data == [COLLECTION_MODE]
    assert page.me == ME
    assert collections.detail_tokens == ["user-token"]
    assert game.collection_calls == [CID]


@pytest.mark.parametrize("viewer", [GUEST, ANONYMOUS])
async def test_collection_page_guest_and_anonymous_have_no_me(viewer: Viewer) -> None:
    page = await GetCollectionPage(FakeCollections(), FakeGame(), concurrency=10)(viewer, CID)
    assert page.me is None
    assert page.blocks.collection.is_ok
    assert page.personal is (viewer.token is not None)


async def test_collection_page_unverified_token_trusts_collections_me() -> None:
    page = await GetCollectionPage(
        FakeCollections(answer=detail(me=None)), FakeGame(), concurrency=10
    )(UNVERIFIED, CID)
    assert page.me is None


async def test_collection_page_game_down_keeps_card() -> None:
    game = FakeGame(collection=UpstreamUnavailableError("game", "http_503"))
    page = await GetCollectionPage(FakeCollections(), game, concurrency=10)(USER, CID)
    assert page.blocks.collection.is_ok
    assert page.blocks.modes.status is BlockStatus.UNAVAILABLE
    assert page.me == ME


async def test_collection_page_game_not_projected_yet_is_unavailable_modes() -> None:
    game = FakeGame(collection=UpstreamRejectedError("game", 404, "game.collection_not_found"))
    page = await GetCollectionPage(FakeCollections(), game, concurrency=10)(USER, CID)
    assert page.blocks.collection.is_ok
    assert page.blocks.modes.status is BlockStatus.UNAVAILABLE


async def test_collection_page_collections_down_keeps_modes() -> None:
    game = FakeGame(collection=[COLLECTION_MODE])
    page = await GetCollectionPage(FakeCollections(answer=DOWN), game, concurrency=10)(USER, CID)
    assert page.blocks.collection.status is BlockStatus.UNAVAILABLE
    assert page.blocks.modes.is_ok
    assert page.me is None


async def test_collection_page_not_found_is_404_with_collections_code() -> None:
    missing = UpstreamRejectedError("collections", 404, "collections.not_found")
    with pytest.raises(NotFoundError) as caught:
        await GetCollectionPage(FakeCollections(answer=missing), FakeGame(), concurrency=10)(
            USER, CID
        )
    assert caught.value.code == "collections.not_found"


# ----------------------------------------------------------------------------- медиатека
async def test_library_requires_user_without_calling_library() -> None:
    library = FakeLibrary()
    with pytest.raises(UnauthenticatedError):
        await GetLibraryPage(library, concurrency=10)(ANONYMOUS)
    with pytest.raises(ForbiddenError) as caught:
        await GetLibraryPage(library, concurrency=10)(GUEST)
    assert caught.value.code == "auth.guest_forbidden"
    with pytest.raises(ForbiddenError):
        await GetLibraryShelfPage(library, concurrency=10, items_limit=50)(
            GUEST, "system:watched", cursor=None
        )
    assert library.calls == []


async def test_library_page_shelves_and_degradation() -> None:
    page = await GetLibraryPage(FakeLibrary(), concurrency=10)(USER)
    assert page.blocks.shelves.data == [shelf()]
    down = await GetLibraryPage(
        FakeLibrary(shelves_answer=UpstreamUnavailableError("library", "timeout")),
        concurrency=10,
    )(USER)
    assert down.blocks.shelves.status is BlockStatus.UNAVAILABLE


async def test_library_unverified_guest_token_rejection_is_forwarded() -> None:
    library = FakeLibrary(
        shelves_answer=UpstreamRejectedError("library", 403, "auth.guest_forbidden")
    )
    with pytest.raises(ForbiddenError) as caught:
        await GetLibraryPage(library, concurrency=10)(UNVERIFIED)
    assert caught.value.code == "auth.guest_forbidden"


async def test_shelf_page_passes_cursor_and_limit() -> None:
    library = FakeLibrary()
    page = await GetLibraryShelfPage(library, concurrency=10, items_limit=40)(
        USER, "system:watch_later", cursor="c1"
    )
    assert page.blocks.shelf.data is not None
    assert page.blocks.shelf.data.next_cursor == "c2"
    assert library.calls == [("user-token", "system:watch_later", "c1", "40")]


async def test_foreign_shelf_is_404() -> None:
    library = FakeLibrary(
        items_answer=UpstreamRejectedError("library", 404, "library.shelf_not_found")
    )
    with pytest.raises(NotFoundError) as caught:
        await GetLibraryShelfPage(library, concurrency=10, items_limit=50)(
            USER, "0192a3b4-c5d6-7e8f-9a0b-000000000d01", cursor=None
        )
    assert caught.value.code == "library.shelf_not_found"
