"""Use-cases комментариев на двойниках портов: права, окна, лимиты, события."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from social_fakes import Harness, example_config
from social_service.application.comments import Actor, CommentService
from social_service.domain.discussion import CollectionRef, Comment
from social_service.domain.discussion_events import CommentDeleted, CommentPosted

from gv_core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)
from gv_core.ids import uuid7
from gv_text.ugc import UgcPolicy

pytestmark = pytest.mark.unit

COLLECTION = UUID(int=100)
OWNER = UUID(int=2)
AUTHOR = Actor(UUID(int=1))
OTHER = Actor(UUID(int=3))
MODERATOR = Actor(UUID(int=4), perms=frozenset({"social.comments.delete_any"}))


def _setup(*, published: bool = True, owner: UUID | None = OWNER) -> tuple[Harness, CommentService]:
    h = Harness()
    h.world.collections[COLLECTION] = CollectionRef(
        COLLECTION, owner, "Хиты 90-х", published=published, version=1
    )
    h.world.nicknames[AUTHOR.user_id] = "Alice"
    service = CommentService(
        h.uow,
        h.config,
        UgcPolicy.from_entries(["плохоеслово"]),
        h.limiter,
        h.clock,
        uuid7,
    )
    return h, service


async def test_post_creates_comment_and_event_without_text() -> None:
    h, service = _setup()
    dto = await service.post(AUTHOR, COLLECTION, "  Классный   сборник ", "key-aaaaaaaaaaaa")
    assert dto.body == "Классный сборник"
    assert dto.author_nickname == "Alice"
    assert dto.can_edit and dto.can_delete
    (event,) = h.events("CommentPosted")
    assert isinstance(event, CommentPosted)
    assert event.notify_owner is True
    assert event.collection_author_id == OWNER
    assert event.collection_title == "Хиты 90-х"
    assert "body" not in event.payload()


async def test_post_by_collection_author_does_not_notify_owner() -> None:
    h, service = _setup()
    await service.post(Actor(OWNER), COLLECTION, "Спасибо!", "key-aaaaaaaaaaaa")
    (event,) = h.events("CommentPosted")
    assert isinstance(event, CommentPosted)
    assert event.notify_owner is False


async def test_post_is_idempotent_by_key() -> None:
    h, service = _setup()
    first = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    second = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    assert first.id == second.id
    assert len(h.world.comments) == 1
    assert len(h.events("CommentPosted")) == 1


async def test_post_same_key_other_body_conflicts() -> None:
    _, service = _setup()
    await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    with pytest.raises(ConflictError) as err:
        await service.post(AUTHOR, COLLECTION, "Пока", "key-aaaaaaaaaaaa")
    assert err.value.code == "idempotency.key_reused"


@pytest.mark.parametrize("collection_published", [False])
async def test_post_to_unpublished_collection_is_not_found(collection_published: bool) -> None:
    _, service = _setup(published=collection_published)
    with pytest.raises(NotFoundError) as err:
        await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    assert err.value.code == "social.collection_not_found"


async def test_post_unknown_collection_is_not_found() -> None:
    _, service = _setup()
    with pytest.raises(NotFoundError):
        await service.post(AUTHOR, UUID(int=555), "Привет", "key-aaaaaaaaaaaa")


async def test_post_denylist_and_length_are_rejected() -> None:
    h, service = _setup()
    for text in ("это плохоеслово тут", "я" * 501):
        with pytest.raises(InvalidInputError) as err:
            await service.post(AUTHOR, COLLECTION, text, "key-" + str(len(text)).zfill(12))
        assert err.value.code == "social.comment_rejected"
    assert not h.world.comments


async def test_post_respects_config_max_length() -> None:
    h, service = _setup()
    h.config.config = example_config(comments={"max_length": 10})
    with pytest.raises(InvalidInputError):
        await service.post(AUTHOR, COLLECTION, "я" * 11, "key-aaaaaaaaaaaa")


async def test_muted_user_cannot_write() -> None:
    _, service = _setup()
    muted = Actor(AUTHOR.user_id, muted=True)
    with pytest.raises(ForbiddenError) as err:
        await service.post(muted, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    assert err.value.code == "social.chat_muted"


async def test_rate_limit_per_minute() -> None:
    h, service = _setup()
    h.config.config = example_config(comments={"per_minute": 2})
    for i in range(2):
        await service.post(AUTHOR, COLLECTION, f"Коммент {i}", f"key-{i:012d}")
    with pytest.raises(RateLimitedError) as err:
        await service.post(AUTHOR, COLLECTION, "Третий", "key-000000000002")
    assert err.value.code == "social.comments_rate_limit"
    assert len(h.world.comments) == 2


async def test_replay_does_not_spend_rate_limit() -> None:
    h, service = _setup()
    h.config.config = example_config(comments={"per_minute": 1})
    await service.post(AUTHOR, COLLECTION, "Один", "key-aaaaaaaaaaaa")
    await service.post(AUTHOR, COLLECTION, "Один", "key-aaaaaaaaaaaa")


async def test_config_unavailable_is_503() -> None:
    h, service = _setup()
    h.config.available = False
    with pytest.raises(DependencyUnavailableError) as err:
        await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    assert err.value.code == "social.config_unavailable"


async def test_edit_inside_window_then_expired() -> None:
    h, service = _setup()
    dto = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    h.clock.advance(timedelta(seconds=299))
    edited = await service.edit(AUTHOR, dto.id, "Привет, мир")
    assert edited.body == "Привет, мир"
    assert edited.edited_at is not None
    h.clock.advance(timedelta(seconds=10))
    with pytest.raises(ConflictError) as err:
        await service.edit(AUTHOR, dto.id, "Поздно")
    assert err.value.code == "social.comment_edit_expired"


async def test_edit_foreign_or_deleted_is_not_found() -> None:
    h, service = _setup()
    dto = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    with pytest.raises(NotFoundError):
        await service.edit(OTHER, dto.id, "Чужое")
    await service.delete(AUTHOR, dto.id)
    with pytest.raises(NotFoundError):
        await service.edit(AUTHOR, dto.id, "Удалённое")
    assert h.world.comments[dto.id].is_deleted


async def test_edit_checks_denylist() -> None:
    _, service = _setup()
    dto = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    with pytest.raises(InvalidInputError):
        await service.edit(AUTHOR, dto.id, "плохоеслово")


@pytest.mark.parametrize(
    ("actor", "role"),
    [(AUTHOR, "author"), (Actor(OWNER), "collection_owner"), (MODERATOR, "moderator")],
)
async def test_delete_by_permitted_roles_emits_event_once(actor: Actor, role: str) -> None:
    h, service = _setup()
    dto = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    await service.delete(actor, dto.id)
    await service.delete(actor, dto.id)  # повтор - без эффекта
    (event,) = h.events("CommentDeleted")
    assert isinstance(event, CommentDeleted)
    assert event.by_role == role
    assert event.deleted_by == actor.user_id
    assert h.world.comments[dto.id].body == ""


async def test_delete_by_stranger_or_unknown_is_not_found() -> None:
    h, service = _setup()
    dto = await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    with pytest.raises(NotFoundError):
        await service.delete(OTHER, dto.id)
    with pytest.raises(NotFoundError):
        await service.delete(OTHER, UUID(int=777))
    assert not h.events("CommentDeleted")


async def test_page_newest_first_with_cursor_and_viewer_flags() -> None:
    h, service = _setup()
    for i in range(3):
        await service.post(AUTHOR, COLLECTION, f"Коммент {i}", f"key-{i:012d}")
        h.clock.advance(timedelta(seconds=1))
    first = await service.page(OTHER, COLLECTION, None, 2)
    assert [c.body for c in first.items] == ["Коммент 2", "Коммент 1"]
    assert first.total == 3 and first.next_cursor is not None
    assert not any(c.can_edit or c.can_delete for c in first.items)
    second = await service.page(None, COLLECTION, first.next_cursor, 2)
    assert [c.body for c in second.items] == ["Коммент 0"]
    assert second.next_cursor is None


async def test_page_flags_for_author_and_collection_owner() -> None:
    _, service = _setup()
    await service.post(AUTHOR, COLLECTION, "Привет", "key-aaaaaaaaaaaa")
    (mine,) = (await service.page(AUTHOR, COLLECTION, None, 10)).items
    assert mine.can_edit and mine.can_delete
    (owner_view,) = (await service.page(Actor(OWNER), COLLECTION, None, 10)).items
    assert not owner_view.can_edit and owner_view.can_delete


async def test_page_unknown_nickname_falls_back_and_hides_deleted() -> None:
    h, service = _setup()
    ghost = UUID(int=42)
    h.world.comments[UUID(int=1000)] = Comment(
        UUID(int=1000), COLLECTION, ghost, "Здесь был призрак", h.clock.now()
    )
    h.world.comments[UUID(int=1001)] = Comment(
        UUID(int=1001), COLLECTION, ghost, "", h.clock.now(), deleted_at=h.clock.now()
    )
    page = await service.page(None, COLLECTION, None, 10)
    assert [c.author_nickname for c in page.items] == ["Игрок"]
    assert page.total == 1


async def test_page_of_unpublished_collection_is_not_found() -> None:
    _, service = _setup(published=False)
    with pytest.raises(NotFoundError):
        await service.page(None, COLLECTION, None, 10)


async def test_page_rejects_broken_cursor() -> None:
    _, service = _setup()
    with pytest.raises(InvalidInputError):
        await service.page(None, COLLECTION, "bad", 10)
