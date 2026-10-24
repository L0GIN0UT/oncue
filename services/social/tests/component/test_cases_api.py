"""Очередь модерации через HTTP: доступ, переходы, события в outbox, санкция, откат."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from social_service.infrastructure.db import tables_moderation as t

from gv_core.errors import ForbiddenError
from gv_core.ids import uuid7
from gv_testkit import assert_problem

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.component

MODERATE = ("admin.access", "social.moderate")
SENIOR = ("admin.access", "social.moderate", "social.sanctions.account")
REASON = "решение по правилам"
SPAM_TEXT = "очень секретный текст жалобы"


class Env:
    """Кейс по комментарию: жалобы дошли до порога."""

    def __init__(self, social: SocialService) -> None:
        self.s = social
        self.moderator = uuid7()
        self.admin = uuid7()
        self.owner = uuid7()
        self.commenter = uuid7()
        self.collection: UUID
        self.comment: UUID
        self.case: UUID
        self.reporters: list[UUID] = []

    def mod(self, user: UUID | None = None) -> dict[str, str]:
        return self.s.auth(user or self.moderator, perms=MODERATE)

    def senior(self) -> dict[str, str]:
        return self.s.auth(self.admin, perms=SENIOR)

    async def open_case(self, kind: str = "comment") -> UUID:
        s = self.s
        self.collection = await s.publish_collection(self.owner)
        posted = await s.client.post(
            f"/v1/collections/{self.collection}/comments",
            json={"body": "грубый комментарий"},
            headers={**s.auth(self.commenter), **s.key()},
        )
        assert posted.status_code == 201, posted.text
        self.comment = UUID(posted.json()["id"])
        target = self.comment if kind == "comment" else self.collection
        threshold = s.config.config.reports.threshold_per_day
        for _ in range(threshold):
            reporter = uuid7()
            self.reporters.append(reporter)
            filed = await s.client.post(
                "/v1/reports",
                json={
                    "target_kind": kind,
                    "target_id": str(target),
                    "reason_code": "spam",
                    "text": SPAM_TEXT,
                },
                headers={**s.auth(reporter), **s.key()},
            )
            assert filed.status_code == 201, filed.text
        (case,) = await s.rows(t.moderation_cases)
        self.case = case.id
        return case.id

    def url(self, suffix: str = "") -> str:
        return f"/admin/v1/cases/{self.case}{suffix}"

    async def act(
        self, action: str, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> Any:
        return await self.s.client.post(
            self.url(f"/{action}"), json=body, headers={**(headers or self.mod()), **self.s.key()}
        )

    async def version(self) -> int:
        return int((await self.s.client.get(self.url(), headers=self.mod())).json()["version"])


async def test_queue_card_and_filters(social: SocialService) -> None:
    env = Env(social)
    await env.open_case()
    listing = await social.client.get("/admin/v1/cases?status=open", headers=env.mod())
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    (item,) = listing.json()["items"]
    assert item["id"] == str(env.case) and item["reports_count"] == len(env.reporters)
    mine = await social.client.get("/admin/v1/cases?mine=true", headers=env.mod())
    assert mine.json()["items"] == []
    card = (await social.client.get(env.url(), headers=env.mod())).json()
    assert card["target_preview"] == "грубый комментарий"
    assert {r["text"] for r in card["reports"]} == {SPAM_TEXT}
    bad = await social.client.get("/admin/v1/cases?status=nope", headers=env.mod())
    assert bad.status_code == 422


async def test_unknown_case_is_404(social: SocialService) -> None:
    env = Env(social)
    response = await social.client.get(f"/admin/v1/cases/{uuid7()}", headers=env.mod())
    assert_problem(response, 404, "social.case_not_found")


async def test_claim_conflicts(social: SocialService) -> None:
    env = Env(social)
    await env.open_case()
    claimed = await env.act("claim", {"version": 1})
    assert claimed.status_code == 200 and claimed.json()["status"] == "in_work"
    other = await env.act("claim", {"version": 1}, env.mod(uuid7()))
    assert_problem(other, 409, "social.case_already_claimed")
    stale = await env.act("escalate", {"version": 1, "reason": REASON})
    assert_problem(stale, 409, "social.case_version_conflict")


async def test_full_resolution_content_removed_publishes_events_without_text(
    social: SocialService,
) -> None:
    env = Env(social)
    await env.open_case()
    await env.act("claim", {"version": 1})
    key = social.key()
    body = {"version": 2, "decision": "content_removed", "reason": "нарушение п. 3"}
    headers = {**env.mod(), **key}
    first = await social.client.post(env.url("/resolve"), json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "resolved" and first.json()["decision"] == "content_removed"
    replay = await social.client.post(env.url("/resolve"), json=body, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()

    (comment,) = await social.rows(t.comments)
    assert comment.deleted_role == "moderator" and comment.body == ""
    (moderated,) = await social.outbox_events("social.ContentModerated")
    assert moderated.payload["decision"] == "content_removed"
    assert moderated.payload["target_id"] == str(env.comment)
    assert moderated.payload["target_user_id"] == str(env.commenter)
    resolved = await social.outbox_events("social.ReportResolved")
    assert sorted(e.payload["reporter_id"] for e in resolved) == sorted(
        str(r) for r in env.reporters
    )
    assert len(await social.outbox_events("social.CommentDeleted")) == 1
    for event in (moderated, *resolved):
        assert SPAM_TEXT not in event.to_bytes().decode()
        assert not {"text", "reason", "note"} & set(event.payload)
    (audit,) = [r for r in await social.rows(t.moderation_audit) if r.action == "case.resolve"]
    assert audit.reason == "нарушение п. 3" and audit.actor_id == env.moderator

    again = await env.act("resolve", {"version": 3, "decision": "dismissed", "reason": REASON})
    assert_problem(again, 409, "social.case_resolved")


async def test_escalated_case_is_resolved_by_senior_only(social: SocialService) -> None:
    env = Env(social)
    await env.open_case("collection")
    await env.act("claim", {"version": 1})
    escalated = await env.act("escalate", {"version": 2, "reason": REASON})
    assert escalated.json()["status"] == "escalated"
    body = {"version": 3, "decision": "dismissed", "reason": REASON}
    denied = await env.act("resolve", body)
    assert_problem(denied, 403, "social.case_escalated_forbidden")
    ok = await env.act("resolve", body, env.senior())
    assert ok.status_code == 200 and ok.json()["resolved_by"] == str(env.admin)
    assert len(await social.outbox_events("social.ReportResolved")) == len(env.reporters)


async def test_sanctioned_goes_through_granter_and_failure_rolls_everything_back(
    social: SocialService,
) -> None:
    env = Env(social)
    await env.open_case()
    await env.act("claim", {"version": 1})
    sanction = {"kind": "chat", "duration_s": 3600, "reason": "оскорбления"}
    social.granter.error = ForbiddenError("себе нельзя", code="social.self_sanction_forbidden")
    body = {"version": 2, "decision": "sanctioned", "sanction": sanction, "reason": REASON}
    failed = await env.act("resolve", body)
    assert_problem(failed, 403, "social.self_sanction_forbidden")
    (case,) = await social.rows(t.moderation_cases)
    assert (case.status, case.version) == ("in_work", 2)
    assert not await social.outbox_events("social.ContentModerated")

    social.granter.error = None
    ok = await env.act("resolve", body)
    assert ok.status_code == 200, ok.text
    (grant,) = social.granter.grants
    assert grant.user_id == env.commenter and grant.kind == "chat" and grant.duration_s == 3600
    assert grant.actor_id == env.moderator
    assert len(await social.outbox_events("social.ContentModerated")) == 1
