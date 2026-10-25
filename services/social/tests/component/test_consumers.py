"""Consumers social на примерах контрактов: проекции, счётчики, жалобы из оценок, удаление."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from social_service.infrastructure.db import tables_moderation as t

from gv_core.ids import uuid7
from gv_messaging import Outcome

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.component

A = "0192a3b4-c5d6-7e8f-9a0b-000000000002"
B = "0192a3b4-c5d6-7e8f-9a0b-000000000003"
MATCH = "0192a3b4-c5d6-7e8f-9a0b-0000000e0101"


async def test_collection_events_maintain_projection(social: SocialService) -> None:
    cid = uuid7()
    author = uuid7()
    await social.deliver(
        social.event(
            "collections.CollectionPublished",
            collection_id=str(cid),
            author_id=str(author),
            version=2,
        )
    )
    await social.deliver(
        social.event(
            "collections.CollectionUpdated",
            collection_id=str(cid),
            author_id=str(author),
            version=1,
            status="hidden",
        )
    )  # устаревшая версия не откатывает проекцию
    (row,) = await social.rows(t.collections_proj, t.collections_proj.c.id == cid)
    assert (row.status, row.version, row.author_id) == ("published", 2, author)
    await social.deliver(
        social.event(
            "collections.CollectionUpdated",
            collection_id=str(cid),
            author_id=str(author),
            version=3,
            status="hidden",
        )
    )
    await social.deliver(
        social.event("collections.CollectionDeleted", collection_id=str(cid), version=4)
    )
    (row,) = await social.rows(t.collections_proj, t.collections_proj.c.id == cid)
    assert (row.status, row.version) == ("unpublished", 4)


async def test_match_finished_fills_participants_and_counts_once(social: SocialService) -> None:
    first = social.event("match.MatchFinished")
    assert await social.deliver(first) is Outcome.PROCESSED
    assert await social.deliver(first) is Outcome.DUPLICATE  # inbox
    await social.deliver(social.event("match.MatchFinished"))  # тот же матч, другой event_id
    assert await social.count(t.match_participants_proj) == 2
    rows = {r.user_id: r for r in await social.rows(t.behaviour_counters)}
    assert rows[UUID(A)].matches_total == 1 and rows[UUID(A)].forfeits == 0
    assert rows[UUID(B)].matches_total == 1 and rows[UUID(B)].forfeits == 1
    assert all(r.solo is False for r in await social.rows(t.match_participants_proj))


async def test_player_rated_report_creates_single_report(social: SocialService) -> None:
    rated = social.event("match.PlayerRated", kind="report", reason="cheating")
    assert await social.deliver(rated) is Outcome.PROCESSED
    assert await social.deliver(rated) is Outcome.DUPLICATE
    assert (
        await social.deliver(social.event("match.PlayerRated", kind="report", reason="cheating"))
        is Outcome.PROCESSED
    )
    (report,) = await social.rows(t.reports)
    assert (str(report.author_id), str(report.target_user_id), report.reason_code) == (
        A,
        B,
        "cheating",
    )
    assert str(report.context_match_id) == MATCH
    assert len(await social.outbox_events("social.ReportFiled")) == 1


async def test_player_rated_report_without_player_reasons_is_skipped_not_retried(
    social: SocialService,
) -> None:
    from social_fakes import example_config

    only_content = [
        {"code": "spam", "target_kinds": ["comment"], "title": {"ru": "Спам"}},
    ]
    social.config.config = example_config(reports={"reasons": only_content})
    outcome = await social.deliver(
        social.event("match.PlayerRated", kind="report", reason="cheating")
    )
    assert outcome is Outcome.PROCESSED  # без ретраев и DLQ: партицию не блокируем
    assert social.dead_letters.letters == []
    assert await social.count(t.reports) == 0


async def test_player_rated_praise_counts(social: SocialService) -> None:
    await social.deliver(social.event("match.PlayerRated", kind="praise", reason=None))
    (row,) = await social.rows(t.behaviour_counters)
    assert str(row.user_id) == B and row.praises == 1
    assert await social.count(t.reports) == 0


async def test_user_deleted_wipes_comments_and_report_text(social: SocialService) -> None:
    author, reporter = uuid7(), uuid7()
    collection = await social.publish_collection(uuid7())
    comment = await social.client.post(
        f"/v1/collections/{collection}/comments",
        json={"body": "Личное мнение"},
        headers={**social.auth(author), **social.key()},
    )
    report = await social.client.post(
        "/v1/reports",
        json={
            "target_kind": "comment",
            "target_id": comment.json()["id"],
            "reason_code": "spam",
            "text": "ПДн",
        },
        headers={**social.auth(reporter), **social.key()},
    )
    assert report.status_code == 201
    await social.deliver(social.event("identity.UserDeleted", user_id=str(author), version=2))
    await social.deliver(social.event("identity.UserDeleted", user_id=str(reporter), version=2))
    (row,) = await social.rows(t.comments)
    assert row.body == "" and row.deleted_role == "account_deleted" and row.deleted_by is None
    (rep,) = await social.rows(t.reports)
    assert rep.text is None
    (event,) = await social.outbox_events("social.CommentDeleted")
    assert event.payload["by_role"] == "account_deleted" and event.payload["deleted_by"] is None
    page = await social.client.get(f"/v1/collections/{collection}/comments")
    assert page.json()["items"] == []


async def test_broken_payload_goes_to_dead_letters(social: SocialService) -> None:
    broken = social.event("collections.CollectionPublished")
    broken = broken.model_copy(update={"payload": {"collection_id": "not-a-uuid"}})
    await social.deliver(broken)
    assert [d["reason"] for d in social.dead_letters.letters] == ["permanent"]


async def test_collection_title_comes_from_event_and_survives_old_events(
    social: SocialService,
) -> None:
    """ADR 0067: название берётся из события; событие старого формата его не затирает."""
    cid = uuid7()
    author = str(uuid7())
    await social.deliver(
        social.event(
            "collections.CollectionPublished",
            collection_id=str(cid),
            author_id=author,
            version=1,
            title="Саундтреки 90-х",
        )
    )
    (row,) = await social.rows(t.collections_proj, t.collections_proj.c.id == cid)
    assert row.title == "Саундтреки 90-х"

    old = social.event(
        "collections.CollectionUpdated", collection_id=str(cid), author_id=author, version=2
    )
    payload = {k: v for k, v in old.payload.items() if k != "title"}
    await social.deliver(old.model_copy(update={"payload": payload}))
    (row,) = await social.rows(t.collections_proj, t.collections_proj.c.id == cid)
    assert (row.title, row.version) == ("Саундтреки 90-х", 2)
