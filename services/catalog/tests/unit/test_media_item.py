"""Агрегат MediaItem: создание, слияние импорта с ручными алиасами, блокировка."""

from __future__ import annotations

import uuid

import pytest
from catalog_service.domain.events import (
    MediaItemBlocked,
    MediaItemCreated,
    MediaItemUnblocked,
    MediaItemUpdated,
)
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import (
    AliasDraft,
    AliasKind,
    AliasSource,
    ExternalSource,
    ItemData,
    ItemStatus,
    MediaKind,
    MediaMetrics,
    PersonCredit,
    PersonRole,
    SourceMediaDraft,
    SourceMediaKind,
    SourceMediaOrigin,
)
from catalog_service.domain.normalization import TitleNormalizer

from gv_core.errors import ConflictError, DomainError, InvalidInputError
from gv_core.ids import uuid7
from gv_messaging.envelope import EventEnvelope
from gv_testkit import FakeClock, assert_event_matches_schema

pytestmark = pytest.mark.unit

N = TitleNormalizer()
YEAR = 2026
NOLAN = PersonCredit(uuid7(), "Кристофер Нолан", PersonRole.DIRECTOR, 0)


def film_data(**overrides: object) -> ItemData:
    base: dict[str, object] = {
        "kind": MediaKind.FILM,
        "title": "Интерстеллар",
        "original_title": "Interstellar",
        "year": 2014,
        "aliases": (AliasDraft("Interstellar", AliasKind.ALT, "en"),),
        "persons": (NOLAN,),
        "genres": ("Sci-Fi", "Drama", "drama"),
        "external_refs": {ExternalSource.TMDB: "157336"},
        "metrics": MediaMetrics(popularity=120.5, vote_average=8.4, vote_count=35000),
        "source_media": (
            SourceMediaDraft(
                SourceMediaKind.POSTER, SourceMediaOrigin.TMDB, "https://image.tmdb.org/p.jpg"
            ),
        ),
    }
    base.update(overrides)
    return ItemData(**base)  # type: ignore[arg-type]


def new_item(**overrides: object) -> MediaItem:
    return MediaItem.create(uuid7(), film_data(**overrides), N, current_year=YEAR)


def as_envelope(item: MediaItem) -> list[EventEnvelope]:
    return [
        EventEnvelope.from_domain_event(e, producer="catalog", clock=FakeClock())
        for e in item.pull_events()
    ]


def test_create_records_valid_created_event() -> None:
    item = new_item()
    [envelope] = as_envelope(item)
    assert envelope.event_type == "catalog.MediaItemCreated"
    assert envelope.topic == "catalog.media_item.v1"
    assert_event_matches_schema(envelope)
    payload = envelope.payload
    assert payload["version"] == 1
    assert payload["status"] == "active"
    assert payload["genres"] == ["sci-fi", "drama"]
    norms = {a["normalized"]: a["kind"] for a in payload["aliases"]}
    assert norms["интерстеллар"] == "title"
    assert norms["interstellar"] == "alt"
    assert payload["metrics"] == {"popularity": 120.5, "vote_average": 8.4, "vote_count": 35000}


def test_create_adds_translit_aliases() -> None:
    item = MediaItem.create(
        uuid7(), film_data(original_title=None, aliases=()), N, current_year=YEAR
    )
    kinds = {a.normalized: a.kind for a in item.aliases}
    assert kinds == {"интерстеллар": AliasKind.TITLE, "interstellar": AliasKind.TRANSLIT}


def test_seed_external_ref_is_not_published() -> None:
    item = new_item(external_refs={ExternalSource.SEED: "films:1", ExternalSource.TMDB: "1"})
    assert item.snapshot()["external_refs"] == {"tmdb": "1"}


@pytest.mark.parametrize("year", [1869, YEAR + 3])
def test_year_out_of_range_rejected(year: int) -> None:
    with pytest.raises(DomainError) as exc:
        new_item(year=year)
    assert exc.value.code == "catalog.invalid_year"


@pytest.mark.parametrize("year", [1870, YEAR + 2, None])
def test_year_bounds_accepted(year: int | None) -> None:
    assert new_item(year=year).year == year


def test_empty_title_rejected() -> None:
    with pytest.raises(DomainError):
        new_item(title="   ")


def test_negative_metric_rejected() -> None:
    with pytest.raises(InvalidInputError):
        MediaMetrics(vote_count=-1)


def test_track_subtitle_lists_artists() -> None:
    queen = PersonCredit(uuid7(), "Queen", PersonRole.ARTIST, 0)
    item = MediaItem.create(
        uuid7(),
        ItemData(kind=MediaKind.TRACK, title="Bohemian Rhapsody", persons=(queen,)),
        N,
        current_year=YEAR,
    )
    assert item.domain.value == "music"
    assert item.subtitle == "Queen"


def test_reimport_same_data_is_noop() -> None:
    item = new_item()
    item.pull_events()
    assert item.apply_import(film_data(), N, current_year=YEAR) == ()
    assert item.version == 1
    assert item.pending_events == ()


def test_reimport_changes_emit_updated_with_changed_fields() -> None:
    item = new_item()
    item.pull_events()
    changed = item.apply_import(
        film_data(metrics=MediaMetrics(popularity=200.0)), N, current_year=YEAR
    )
    assert changed == ("metrics",)
    [envelope] = as_envelope(item)
    assert envelope.event_type == "catalog.MediaItemUpdated"
    assert envelope.payload["changed_fields"] == ["metrics"]
    assert envelope.payload["version"] == 2
    assert_event_matches_schema(envelope)


def test_manual_aliases_survive_reimport() -> None:
    item = new_item()
    item.replace_aliases(
        [
            AliasDraft("Интерстеллар", AliasKind.TITLE, "ru"),
            AliasDraft("Интер", AliasKind.SHORT, "ru"),
        ],
        N,
    )
    item.pull_events()

    item.apply_import(
        film_data(aliases=(AliasDraft("Interstellar (2014)", AliasKind.ALT, "en"),)),
        N,
        current_year=YEAR,
    )

    by_norm = {a.normalized: a for a in item.aliases}
    assert by_norm["интер"].source is AliasSource.MANUAL
    # Совпавший с ручным импортный алиас не дублируется и не перетирает ручной.
    assert by_norm["интерстеллар"].source is AliasSource.MANUAL
    assert by_norm["интерстеллар"].lang == "ru"
    assert by_norm["interstellar 2014"].source is AliasSource.IMPORT
    assert len({a.normalized for a in item.aliases}) == len(item.aliases)


def test_import_replaces_previous_import_aliases() -> None:
    item = new_item(aliases=(AliasDraft("Старое имя", AliasKind.ALT),))
    item.apply_import(film_data(aliases=()), N, current_year=YEAR)
    assert "старое имя" not in {a.normalized for a in item.aliases}


def test_replace_aliases_requires_title_kind() -> None:
    item = new_item()
    with pytest.raises(DomainError) as exc:
        item.replace_aliases([AliasDraft("Интер", AliasKind.SHORT)], N)
    assert exc.value.code == "catalog.title_alias_required"


def test_replace_aliases_emits_updated() -> None:
    item = new_item()
    item.pull_events()
    assert item.replace_aliases([AliasDraft("Интерстеллар", AliasKind.TITLE)], N)
    [event] = item.pull_events()
    assert isinstance(event, MediaItemUpdated)
    assert event.changed_fields == ("aliases",)


def test_block_and_unblock_bump_version_and_emit_events() -> None:
    item = new_item()
    item.pull_events()
    actor = uuid7()
    item.block("copyright.claim", actor)
    assert item.status is ItemStatus.BLOCKED
    assert not item.is_active
    item.unblock(actor)
    envelopes = as_envelope(item)
    assert [e.event_type for e in envelopes] == [
        "catalog.MediaItemBlocked",
        "catalog.MediaItemUnblocked",
    ]
    assert [e.payload["version"] for e in envelopes] == [2, 3]
    for envelope in envelopes:
        assert_event_matches_schema(envelope)


def test_double_block_is_conflict() -> None:
    item = new_item()
    item.block("moderation", None)
    with pytest.raises(ConflictError):
        item.block("moderation", None)


def test_unblock_active_is_conflict() -> None:
    with pytest.raises(ConflictError):
        new_item().unblock(None)


@pytest.mark.parametrize("code", ["Bad Code", "", "x" * 65, "1abc"])
def test_block_rejects_bad_reason_code(code: str) -> None:
    with pytest.raises(DomainError):
        new_item().block(code, None)


def test_import_on_blocked_item_keeps_blocked_status() -> None:
    item = new_item()
    item.block("moderation", None)
    item.pull_events()
    item.apply_import(film_data(year=2015), N, current_year=YEAR)
    [event] = item.pull_events()
    assert isinstance(event, MediaItemUpdated)
    assert event.snapshot["status"] == "blocked"


def test_import_kind_mismatch_is_conflict() -> None:
    item = new_item()
    with pytest.raises(ConflictError):
        item.apply_import(ItemData(kind=MediaKind.TRACK, title="x"), N, current_year=YEAR)


def test_source_media_ids_are_stable_across_imports() -> None:
    item = new_item()
    first = item.source_media[0].id
    item.apply_import(film_data(year=2015), N, current_year=YEAR)
    assert item.source_media[0].id == first
    assert isinstance(first, uuid.UUID)


def test_events_are_typed() -> None:
    item = new_item()
    item.block("moderation", None)
    item.unblock(None)
    kinds = [type(e) for e in item.pull_events()]
    assert kinds == [MediaItemCreated, MediaItemBlocked, MediaItemUnblocked]


def test_restore_has_no_events_and_keeps_persisted_version() -> None:
    original = new_item()
    restored = MediaItem.restore(
        original.id,
        kind=original.kind,
        status=original.status,
        version=7,
        title=original.title,
        original_title=original.original_title,
        year=original.year,
        aliases=original.aliases,
        persons=original.persons,
        genres=original.genres,
        external_refs=original.external_refs,
        metrics=original.metrics,
        source_media=original.source_media,
    )
    assert restored.pending_events == ()
    assert restored.persisted_version == 7
    assert restored.apply_import(film_data(), N, current_year=YEAR) == ()
