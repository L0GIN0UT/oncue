"""Комментарии через HTTP: доступ, ошибки, события, удаление, правка, чтение без токена."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from social_service.infrastructure.db import tables_moderation as t

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_testkit import assert_problem

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.component


async def _post(
    s: SocialService, collection: UUID, user: UUID, body: str = "Классный сборник"
) -> dict[str, object]:
    response = await s.client.post(
        f"/v1/collections/{collection}/comments",
        json={"body": body},
        headers={**s.auth(user), **s.key()},
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data


async def test_post_by_user_blocked_by_owner_does_not_notify_owner(social: SocialService) -> None:
    from sqlalchemy import text

    owner, author = uuid7(), uuid7()
    collection = await social.publish_collection(owner)
    assert social.infra.sessions is not None
    async with social.infra.sessions() as session, session.begin():
        await session.execute(
            text("INSERT INTO blocks (blocker_id, blocked_id, created_at) VALUES (:a, :b, now())"),
            {"a": owner, "b": author},
        )
    await _post(social, collection, author)
    (event,) = await social.outbox_events("social.CommentPosted")
    assert event.payload["notify_owner"] is False


async def test_post_requires_account(social: SocialService) -> None:
    collection = await social.publish_collection(uuid7())
    url = f"/v1/collections/{collection}/comments"
    body = {"json": {"body": "Привет"}}
    assert_problem(
        await social.client.post(url, **body, headers=social.key()), 401, "auth.unauthenticated"
    )
    guest = await social.client.post(url, **body, headers={**social.guest(), **social.key()})
    assert guest.status_code == 403


async def test_post_publishes_event_and_returns_comment(social: SocialService) -> None:
    owner, author = uuid7(), uuid7()
    await social.add_nickname(author, "Alice")
    collection = await social.publish_collection(owner)
    comment = await _post(social, collection, author)
    assert comment["author_nickname"] == "Alice"
    assert comment["can_edit"] is True and comment["can_delete"] is True
    (event,) = await social.outbox_events("social.CommentPosted")
    assert event.payload["comment_id"] == comment["id"]
    assert event.payload["notify_owner"] is True
    assert event.payload["collection_author_id"] == str(owner)
    assert "body" not in event.payload
    assert event.topic == "social.social.v1"


async def test_post_replay_by_key_returns_same_comment(social: SocialService) -> None:
    collection = await social.publish_collection(uuid7())
    headers = {**social.auth(), **social.key()}
    url = f"/v1/collections/{collection}/comments"
    first = await social.client.post(url, json={"body": "Привет"}, headers=headers)
    second = await social.client.post(url, json={"body": "Привет"}, headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert await social.count(t.comments) == 1
    assert len(await social.outbox_events("social.CommentPosted")) == 1


async def test_post_errors(social: SocialService) -> None:
    author = uuid7()
    collection = await social.publish_collection(uuid7())
    url = f"/v1/collections/{collection}/comments"

    async def post(url: str, body: str, **auth: object) -> object:
        return await social.client.post(
            url,
            json={"body": body},
            headers={**social.auth(author, **auth), **social.key()},  # type: ignore[arg-type]
        )

    assert_problem(
        await post(f"/v1/collections/{uuid7()}/comments", "Привет"),
        404,
        "social.collection_not_found",
    )  # type: ignore[arg-type]
    hidden = await social.publish_collection(uuid7(), status="hidden")
    assert_problem(
        await post(f"/v1/collections/{hidden}/comments", "Привет"),
        404,
        "social.collection_not_found",
    )  # type: ignore[arg-type]
    assert_problem(await post(url, "ну блять"), 422, "social.comment_rejected")  # type: ignore[arg-type]
    assert_problem(
        await post(url, "Привет", sanctions=int(Sanction.CHAT_MUTE)), 403, "social.chat_muted"
    )  # type: ignore[arg-type]
    assert_problem(
        await post(url, "Привет", sanctions=int(Sanction.COMMENTS)), 403, "social.chat_muted"
    )  # type: ignore[arg-type]
    too_long = await social.client.post(
        url, json={"body": "я" * 501}, headers={**social.auth(), **social.key()}
    )
    assert too_long.status_code == 422
    assert await social.count(t.comments) == 0


async def test_rate_limit_per_minute(social: SocialService) -> None:
    author = uuid7()
    collection = await social.publish_collection(uuid7())
    limit = social.config.config.comments.per_minute
    for i in range(limit):
        await _post(social, collection, author, f"Комментарий {i}")
    response = await social.client.post(
        f"/v1/collections/{collection}/comments",
        json={"body": "Лишний"},
        headers={**social.auth(author), **social.key()},
    )
    assert_problem(response, 429, "social.comments_rate_limit")


async def test_config_unavailable_is_503(social: SocialService) -> None:
    collection = await social.publish_collection(uuid7())
    social.config.available = False
    response = await social.client.post(
        f"/v1/collections/{collection}/comments",
        json={"body": "Привет"},
        headers={**social.auth(), **social.key()},
    )
    assert_problem(response, 503, "social.config_unavailable")


async def test_list_is_public_newest_first_with_viewer_flags(social: SocialService) -> None:
    owner, author = uuid7(), uuid7()
    collection = await social.publish_collection(owner)
    for i in range(3):
        await _post(social, collection, author, f"Комментарий {i}")
        social.clock.advance(timedelta(seconds=1))
    url = f"/v1/collections/{collection}/comments"
    guest_page = await social.client.get(url, params={"limit": 2})
    assert guest_page.status_code == 200
    data = guest_page.json()
    assert [c["body"] for c in data["items"]] == ["Комментарий 2", "Комментарий 1"]
    assert data["total"] == 3
    assert not any(c["can_edit"] or c["can_delete"] for c in data["items"])
    rest = await social.client.get(url, params={"limit": 2, "cursor": data["next_cursor"]})
    assert [c["body"] for c in rest.json()["items"]] == ["Комментарий 0"]
    assert rest.json()["next_cursor"] is None
    owner_page = await social.client.get(url, headers=social.auth(owner))
    assert all(c["can_delete"] and not c["can_edit"] for c in owner_page.json()["items"])
    unknown = await social.client.get(f"/v1/collections/{uuid7()}/comments")
    assert_problem(unknown, 404, "social.collection_not_found")


async def test_edit_in_window_then_409_and_foreign_404(social: SocialService) -> None:
    author = uuid7()
    collection = await social.publish_collection(uuid7())
    comment = await _post(social, collection, author)
    url = f"/v1/comments/{comment['id']}"
    social.clock.advance(timedelta(minutes=4))
    ok = await social.client.patch(url, json={"body": "Поправил"}, headers=social.auth(author))
    assert ok.status_code == 200 and ok.json()["body"] == "Поправил"
    assert ok.json()["edited_at"] is not None
    stranger = await social.client.patch(url, json={"body": "Чужое"}, headers=social.auth())
    assert_problem(stranger, 404, "social.comment_not_found")
    social.clock.advance(timedelta(minutes=2))
    late = await social.client.patch(url, json={"body": "Поздно"}, headers=social.auth(author))
    assert_problem(late, 409, "social.comment_edit_expired")


@pytest.mark.parametrize(
    ("who", "role"),
    [("author", "author"), ("owner", "collection_owner"), ("moderator", "moderator")],
)
async def test_delete_by_allowed_roles(social: SocialService, who: str, role: str) -> None:
    owner, author, moderator = uuid7(), uuid7(), uuid7()
    collection = await social.publish_collection(owner)
    comment = await _post(social, collection, author)
    actor = {
        "author": social.auth(author),
        "owner": social.auth(owner),
        "moderator": social.auth(moderator, perms=("social.comments.delete_any",)),
    }[who]
    url = f"/v1/comments/{comment['id']}"
    assert (await social.client.delete(url, headers=actor)).status_code == 204
    assert (await social.client.delete(url, headers=actor)).status_code == 204  # повтор
    (event,) = await social.outbox_events("social.CommentDeleted")
    assert event.payload["by_role"] == role
    page = await social.client.get(f"/v1/collections/{collection}/comments")
    assert page.json()["items"] == []


async def test_delete_by_stranger_is_404_and_requires_auth(social: SocialService) -> None:
    collection = await social.publish_collection(uuid7())
    comment = await _post(social, collection, uuid7())
    url = f"/v1/comments/{comment['id']}"
    assert_problem(
        await social.client.delete(url, headers=social.auth()), 404, "social.comment_not_found"
    )
    assert_problem(await social.client.delete(url), 401, "auth.unauthenticated")
    assert await social.count(t.comments, t.comments.c.deleted_at.is_(None)) == 1
