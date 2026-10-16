"""Агрегат ``UserLibrary`` (L2): ленивые полки, ``exclusive_with``, домены, лимиты, события."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from library_service.domain.errors import (
    DomainMismatchError,
    ItemUnavailableError,
    ShelfFullError,
    ShelfNotFoundError,
    ShelvesLimitError,
)
from library_service.domain.events import ItemRemoved, ItemSaved
from library_service.domain.library import (
    CatalogEntry,
    ItemAdded,
    ItemDeleted,
    ItemStatus,
    LibraryLimits,
    PersonEntry,
    ShelfCreated,
    ShelfDeleted,
    UserLibrary,
)
from library_service.domain.shelves import SaveSource, ShelfKind, ShelfRef
from library_service.infrastructure.shelves_config import load_shelf_catalog

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
CATALOG = load_shelf_catalog()
WATCH_LATER = ShelfRef.system("watch_later")
WATCHED = ShelfRef.system("watched")
FAVORITE = ShelfRef.system("favorite")


def entry(domain: str = "film", status: ItemStatus = ItemStatus.ACTIVE) -> CatalogEntry:
    return CatalogEntry(
        media_item_id=uuid7(),
        domain=domain,
        kind="film" if domain == "film" else "track",
        status=status,
        title="Единица",
        year=2014,
        persons=(),
        version=1,
    )


def library(*, version: int = 0, shelves_max: int = 50, items_max: int = 2000) -> UserLibrary:
    return UserLibrary(
        uuid7(),
        version,
        [],
        [],
        catalog=CATALOG,
        limits=LibraryLimits(custom_shelves_max=shelves_max, items_per_shelf_max=items_max),
    )


def test_save_creates_system_shelf_lazily_and_records_event() -> None:
    lib = library(version=7)
    item = entry()
    result = lib.save(WATCH_LATER, item, SaveSource.REVEAL, now=NOW, new_shelf_id=uuid7())
    assert result.changed and result.shelves == (WATCH_LATER,)
    shelf = lib.by_key("watch_later")
    assert shelf is not None and shelf.items_count == 1 and shelf.kind is ShelfKind.SYSTEM
    assert [type(c) for c in lib.changes] == [ShelfCreated, ItemAdded]
    [event] = lib.pending_events
    assert isinstance(event, ItemSaved)
    assert event.version == 8 and lib.version == 8
    assert event.aggregate_id == str(lib.user_id)
    assert event.payload()["shelf_key"] == "watch_later"
    assert event.payload()["source"] == "reveal"


def test_repeat_save_is_noop_without_event() -> None:
    lib = library()
    item = entry()
    lib.save(FAVORITE, item, SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    lib.pull_events()
    lib.changes.clear()
    again = lib.save(FAVORITE, item, SaveSource.RESULTS, now=NOW, new_shelf_id=uuid7())
    assert not again.changed
    assert lib.pending_events == () and lib.changes == []
    assert lib.version == 1


def test_exclusive_with_moves_item_between_system_shelves() -> None:
    lib = library()
    item = entry()
    lib.save(WATCH_LATER, item, SaveSource.REVEAL, now=NOW, new_shelf_id=uuid7())
    result = lib.save(WATCHED, item, SaveSource.RESULTS, now=NOW, new_shelf_id=uuid7())
    assert result.shelves == (WATCHED,)
    assert result.removed_from == (WATCH_LATER,)
    events = lib.pull_events()
    assert [
        (type(e).__name__, e.payload()["shelf_key"], e.payload()["source"]) for e in events
    ] == [
        ("ItemSaved", "watch_later", "reveal"),
        ("ItemSaved", "watched", "results"),
        ("ItemRemoved", "watch_later", "exclusive"),
    ]
    assert [e.payload()["version"] for e in events] == [1, 2, 3]
    later = lib.by_key("watch_later")
    assert later is not None and later.items_count == 0
    # Обратно: «смотреть позже» тоже исключает «просмотрено» (watched ↔ watch_later).
    back = lib.save(WATCH_LATER, item, SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    assert back.removed_from == (WATCHED,)


def test_favorite_is_independent_of_exclusive_shelves() -> None:
    lib = library()
    item = entry()
    lib.save(WATCHED, item, SaveSource.RESULTS, now=NOW, new_shelf_id=uuid7())
    result = lib.save(FAVORITE, item, SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    assert result.shelves == (WATCHED, FAVORITE) and result.removed_from == ()


@pytest.mark.parametrize("item", [None, entry(status=ItemStatus.BLOCKED)])
def test_unavailable_item_cannot_be_saved(item: CatalogEntry | None) -> None:
    lib = library()
    with pytest.raises(ItemUnavailableError):
        lib.save(FAVORITE, item, SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    assert lib.changes == [] and lib.shelves == {}


def test_domain_must_be_one_of_shelf_domains() -> None:
    lib = library()
    with pytest.raises(DomainMismatchError) as exc:
        lib.save(WATCH_LATER, entry("music"), SaveSource.REVEAL, now=NOW, new_shelf_id=uuid7())
    assert exc.value.details == {"domain": "music", "allowed": ["film"]}
    assert lib.shelves == {}


def test_shelf_full_limit() -> None:
    lib = library(items_max=1)
    lib.save(FAVORITE, entry(), SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    with pytest.raises(ShelfFullError):
        lib.save(FAVORITE, entry(), SaveSource.CARD, now=NOW, new_shelf_id=uuid7())


def test_custom_shelves_limit_and_lifecycle() -> None:
    lib = library(shelves_max=1)
    shelf = lib.create_shelf("Ужасы", now=NOW, new_shelf_id=uuid7())
    with pytest.raises(ShelvesLimitError) as exc:
        lib.create_shelf("Ещё", now=NOW, new_shelf_id=uuid7())
    assert exc.value.details == {"max": 1}
    film, track = entry("film"), entry("music")
    ref = shelf.ref
    for item in (film, track):
        lib.save(ref, item, SaveSource.COLLECTION, now=NOW, new_shelf_id=uuid7())
    later = NOW + timedelta(minutes=1)
    renamed = lib.rename_shelf(shelf.id, "Ужасы 80-х", now=later)
    assert renamed.title == "Ужасы 80-х" and renamed.updated_at == later
    lib.pull_events()
    assert lib.delete_shelf(shelf.id, now=later) == 2
    events = lib.pull_events()
    assert all(isinstance(e, ItemRemoved) for e in events)
    assert {e.payload()["source"] for e in events} == {"shelf_deleted"}
    assert isinstance(lib.changes[-1], ShelfDeleted)
    assert sum(isinstance(c, ItemDeleted) for c in lib.changes) == 2
    with pytest.raises(ShelfNotFoundError):
        lib.custom(shelf.id)


def test_remove_is_noop_when_absent_or_shelf_not_created() -> None:
    lib = library()
    item = entry()
    assert not lib.remove(WATCHED, item.media_item_id, SaveSource.CARD, now=NOW).changed
    assert lib.shelves == {}
    lib.save(WATCHED, item, SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    assert lib.remove(WATCHED, item.media_item_id, SaveSource.CARD, now=NOW).changed
    assert not lib.remove(WATCHED, item.media_item_id, SaveSource.CARD, now=NOW).changed


def test_foreign_or_system_ids_are_not_custom_shelves() -> None:
    lib = library()
    lib.save(FAVORITE, entry(), SaveSource.CARD, now=NOW, new_shelf_id=uuid7())
    favorite = lib.by_key("favorite")
    assert favorite is not None
    for shelf_id in (favorite.id, uuid7()):
        with pytest.raises(ShelfNotFoundError):
            lib.resolve(ShelfRef(shelf_id=shelf_id))
        with pytest.raises(ShelfNotFoundError):
            lib.rename_shelf(shelf_id, "x", now=NOW)
    with pytest.raises(ShelfNotFoundError):
        lib.resolve(ShelfRef.system("wishlist"))


def test_card_persons_follow_config_roles() -> None:
    item = CatalogEntry(
        media_item_id=UUID(int=1),
        domain="film",
        kind="film",
        status=ItemStatus.ACTIVE,
        title="t",
        year=None,
        persons=(
            PersonEntry("Актёр", "actor", 0),
            PersonEntry("Режиссёр Б", "director", 2),
            PersonEntry("Режиссёр А", "director", 1),
            PersonEntry("Певица", "artist", 0),
        ),
        version=1,
    )
    assert item.card_persons(("artist", "director"), 2) == ["Певица", "Режиссёр А"]
    assert item.card_persons(("director",), 0) == []
