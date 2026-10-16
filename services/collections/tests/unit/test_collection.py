"""Агрегат сборника (K2): инварианты версий, черновика, модерации и событий."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from collections_service.domain.collection import DEFAULT_FEATURED_RANK, Collection, liked_event
from collections_service.domain.errors import (
    CollectionDeletedError,
    CollectionHiddenError,
    DraftConflictError,
    NotCuratedError,
    NotPublishedError,
    VersionConflictError,
)
from collections_service.domain.events import (
    CollectionDeleted,
    CollectionPublished,
    CollectionUpdated,
)
from collections_service.domain.model import (
    CollectionBody,
    Domain,
    DraftBody,
    Item,
    Kind,
    Status,
    Visibility,
)

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
AUTHOR = uuid7()
ITEMS = tuple(Item(media_item_id=uuid7()) for _ in range(5))


def body(items: tuple[Item, ...] = ITEMS, **extra: object) -> CollectionBody:
    values: dict[str, object] = {
        "title": "Кино · кадры",
        "description": None,
        "tags": (),
        "cover": None,
        "default_mode_key": "film.collection.frame",
        "visibility": Visibility.PUBLIC,
        "items": items,
    } | extra
    return CollectionBody(**values)  # type: ignore[arg-type] — значения выше по полям


def new() -> Collection:
    return Collection.create(uuid7(), domain=Domain.FILM, title="Кино", author_id=AUTHOR, now=NOW)


def published() -> Collection:
    c = new()
    c.save_draft(DraftBody(title="Кино"), base_revision=1, client_label=None, now=NOW)
    c.publish(2, body(), NOW)
    c.pull_events()
    return c


def test_create_starts_with_first_revision_and_no_events() -> None:
    c = new()
    assert c.state.status is Status.DRAFT and c.current_revision == 1
    assert c.version == 0 and c.state.content_version == 0
    assert c.pending_events == ()
    assert c.is_author(AUTHOR) and not c.is_author(uuid7()) and not c.is_author(None)


def test_save_draft_requires_current_revision() -> None:
    c = new()
    draft = c.save_draft(DraftBody(title="A"), base_revision=1, client_label="tab", now=NOW)
    assert draft.revision == 2 and draft.client_label == "tab"
    with pytest.raises(DraftConflictError) as exc:
        c.save_draft(DraftBody(title="B"), base_revision=1, client_label=None, now=NOW)
    assert exc.value.details == {"revision": 2}


def test_publish_creates_version_and_snapshot_event() -> None:
    c = new()
    c.save_draft(DraftBody(title="A"), base_revision=1, client_label=None, now=NOW)
    with pytest.raises(DraftConflictError):
        c.publish(1, body(), NOW)
    later = NOW + timedelta(hours=1)
    c.publish(2, body(), later)
    assert c.state.status is Status.PUBLISHED and c.state.draft is None
    assert (c.version, c.state.content_version) == (1, 1)
    assert c.state.first_published_at == later
    (event,) = c.pull_events()
    assert isinstance(event, CollectionPublished)
    payload = event.payload()
    assert payload["status"] == "published" and "custom" not in payload["items"][0]
    assert [i["position"] for i in payload["items"]] == [0, 1, 2, 3, 4]


def test_draft_view_without_draft_is_published_body_with_revision_zero() -> None:
    c = published()
    view = c.draft_view()
    assert view.revision == 0 and view.base_version == 1
    assert view.body.title == "Кино · кадры" and len(view.body.items) == 5


def test_revision_is_monotonic_across_publications() -> None:
    """Нет ABA: ревизия черновика до публикации не совпадёт с новой."""
    c = published()
    assert c.current_revision == 0
    draft = c.save_draft(DraftBody(title="B"), base_revision=0, client_label=None, now=NOW)
    assert draft.revision == 3 and draft.base_version == 1
    with pytest.raises(DraftConflictError):
        c.require_draft(2)


def test_hidden_collection_is_not_editable_and_hide_is_idempotent() -> None:
    c = published()
    actor = uuid7()
    assert c.hide(reason="спам", actor_id=actor, now=NOW) is True
    assert c.hide(reason="спам", actor_id=actor, now=NOW) is False
    (event,) = c.pull_events()
    assert isinstance(event, CollectionUpdated) and event.status == "hidden"
    assert event.version == 2
    with pytest.raises(CollectionHiddenError):
        c.save_draft(DraftBody(title="x"), base_revision=0, client_label=None, now=NOW)
    with pytest.raises(CollectionHiddenError):
        c.require_draft(1)
    assert c.unhide(NOW) is True and c.unhide(NOW) is False
    (shown,) = c.pull_events()
    assert isinstance(shown, CollectionUpdated) and shown.status == "published"
    assert c.state.hidden is None


def test_hide_requires_published() -> None:
    with pytest.raises(NotPublishedError):
        new().hide(reason="r", actor_id=uuid7(), now=NOW)


def test_featured_only_published_and_cleared_on_hide() -> None:
    with pytest.raises(NotPublishedError):
        new().set_featured(featured=True, rank=1, now=NOW)
    c = published()
    c.set_featured(featured=True, rank=None, now=NOW)
    assert c.state.featured_rank == DEFAULT_FEATURED_RANK
    c.hide(reason="r", actor_id=uuid7(), now=NOW)
    assert c.state.featured is False and c.state.featured_rank is None
    c.set_featured(featured=False, rank=5, now=NOW)
    assert c.state.featured_rank is None


def test_remove_item_creates_new_version_without_item() -> None:
    c = published()
    gone = ITEMS[1].media_item_id
    assert c.remove_item(uuid7(), NOW) is False
    assert c.remove_item(gone, NOW) is True
    assert c.state.content_version == 2 and len(c.state.current.items) == 4  # type: ignore[union-attr]
    (event,) = c.pull_events()
    assert isinstance(event, CollectionUpdated)
    assert gone not in {i.media_item_id for i in event.items}
    assert [i.position for i in event.items] == [0, 1, 2, 3]
    assert c.remove_item(gone, NOW) is False
    assert new().remove_item(gone, NOW) is False


def test_delete_published_emits_once_and_blocks_further_changes() -> None:
    c = published()
    assert c.delete(NOW) is True and c.delete(NOW) is False
    (event,) = c.pull_events()
    assert isinstance(event, CollectionDeleted) and event.version == 2
    with pytest.raises(CollectionDeletedError):
        c.save_draft(DraftBody(title="x"), base_revision=0, client_label=None, now=NOW)
    with pytest.raises(CollectionDeletedError):
        c.hide(reason="r", actor_id=uuid7(), now=NOW)
    with pytest.raises(CollectionDeletedError):
        c.unhide(NOW)


def test_delete_draft_emits_nothing() -> None:
    c = new()
    assert c.delete(NOW) is True
    assert c.pull_events() == []


def test_forget_author_purges_drafts_and_anonymizes_published() -> None:
    assert new().forget_author(NOW) is True
    c = published()
    c.save_draft(DraftBody(title="x"), base_revision=0, client_label=None, now=NOW)
    assert c.forget_author(NOW) is False
    assert c.state.author_id is None and c.state.draft is None
    (event,) = c.pull_events()
    assert isinstance(event, CollectionUpdated) and event.author_id is None


def test_curated_is_published_immediately_and_versioned() -> None:
    c = Collection.create_curated(uuid7(), domain=Domain.MUSIC, body=body(), now=NOW)
    assert c.state.kind is Kind.CURATED and c.state.author_id is None
    assert c.state.status is Status.PUBLISHED
    (event,) = c.pull_events()
    assert isinstance(event, CollectionPublished) and event.kind == "curated"
    with pytest.raises(VersionConflictError) as exc:
        c.update_curated(body(), base_version=2, now=NOW)
    assert exc.value.details == {"current_version": 1}
    c.update_curated(body(ITEMS[:4]), base_version=1, now=NOW)
    assert c.state.content_version == 2
    with pytest.raises(NotCuratedError):
        c.save_draft(DraftBody(title="x"), base_revision=0, client_label=None, now=NOW)
    with pytest.raises(NotCuratedError):
        published().update_curated(body(), base_version=1, now=NOW)
    c.delete(NOW)
    with pytest.raises(CollectionDeletedError):
        c.update_curated(body(), base_version=2, now=NOW)


def test_curated_update_while_hidden_stays_hidden() -> None:
    c = Collection.create_curated(uuid7(), domain=Domain.FILM, body=body(), now=NOW)
    c.hide(reason="r", actor_id=uuid7(), now=NOW)
    c.update_curated(body(ITEMS[:3]), base_version=1, now=NOW)
    assert c.state.status is Status.HIDDEN
    assert c.pull_events()[-1].payload()["status"] == "hidden"


def test_liked_event_carries_counter_and_version() -> None:
    cid = uuid7()
    event = liked_event(cid, version=4, user_id=uuid7(), likes_count=12, now=NOW)
    assert event.aggregate_id == str(cid)
    assert (event.payload()["version"], event.payload()["likes_count"]) == (4, 12)


def test_body_json_round_trip() -> None:
    draft = DraftBody(
        title="t",
        description="d",
        tags=("horror",),
        cover="night",
        default_mode_key="film.collection.frame",
        visibility=Visibility.UNLISTED,
        items=(Item(uuid7(), "🎬"), Item(uuid7())),
    )
    assert DraftBody.from_json(draft.to_json()) == draft
    version = body(items=(Item(uuid7(), "🎬"),), tags=("horror",), cover="night")
    assert CollectionBody.from_json(version.to_json()) == version
