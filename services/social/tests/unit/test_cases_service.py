"""Очередь модерации на двойниках портов: переходы, права старшего, версии, события."""

from __future__ import annotations

from uuid import UUID

import pytest
from social_fakes import Harness
from social_service.application.cases import CaseService, SanctionSpec, Staff
from social_service.application.ports.discussion import NewReport
from social_service.domain.discussion import CollectionRef, Comment
from social_service.domain.discussion_events import (
    CommentDeleted,
    ContentModerated,
    ReportResolved,
)

from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

CASE = UUID(int=1000)
COLLECTION = UUID(int=100)
COMMENT = UUID(int=200)
TARGET_USER = UUID(int=2)
REPORTERS = [UUID(int=10), UUID(int=11), UUID(int=12)]
MODERATOR = Staff(UUID(int=50), frozenset({"social.moderate"}))
OTHER_MODERATOR = Staff(UUID(int=51), frozenset({"social.moderate"}))
ADMIN = Staff(UUID(int=52), frozenset({"social.moderate", "social.sanctions.account"}))
REASON = "по правилам сообщества"


def _key() -> str:
    return f"key-{uuid7().hex}"


def _setup(kind: str = "comment") -> tuple[Harness, CaseService]:
    h = Harness()
    target = {"comment": COMMENT, "collection": COLLECTION, "player": TARGET_USER}.get(
        kind, UUID(int=300)
    )
    h.world.collections[COLLECTION] = CollectionRef(
        COLLECTION, TARGET_USER, "Хиты 90-х", published=True, version=1
    )
    h.world.comments[COMMENT] = Comment(
        COMMENT, COLLECTION, TARGET_USER, "грубый текст", h.clock.now()
    )
    h.world.cases[CASE] = {
        "kind": kind,
        "target": target,
        "target_user": TARGET_USER,
        "status": "open",
        "assignee": None,
        "decision": None,
        "resolved_by": None,
        "resolved_at": None,
        "opened_at": h.clock.now(),
        "version": 1,
    }
    for i, author in enumerate(REPORTERS):
        report = NewReport(
            id=UUID(int=900 + i),
            author_id=author,
            target_kind=kind,
            target_id=target,
            target_user_id=TARGET_USER,
            context_match_id=None,
            reason_code="spam",
            text=f"секретный текст жалобы {i}",
            created_at=h.clock.now(),
        )
        h.world.reports.append(report)
        h.world.case_reports.add((CASE, report.id))
    return h, CaseService(h.uow, h.granter, h.clock, uuid7)


async def _claim(service: CaseService, staff: Staff = MODERATOR, version: int = 1) -> dict:  # type: ignore[type-arg]
    return await service.claim(staff, CASE, version, _key())


async def _resolve(
    service: CaseService,
    decision: str = "dismissed",
    *,
    staff: Staff = MODERATOR,
    version: int = 2,
    sanction: SanctionSpec | None = None,
) -> dict:  # type: ignore[type-arg]
    return await service.resolve(
        staff,
        CASE,
        version=version,
        decision=decision,
        sanction=sanction,
        reason=REASON,
        key=_key(),
    )


# ------------------------------------------------------------------ claim / escalate


async def test_claim_moves_open_to_in_work_and_audits() -> None:
    h, service = _setup()
    data = await _claim(service)
    assert (data["status"], data["assignee_id"], data["version"]) == (
        "in_work",
        str(MODERATOR.user_id),
        2,
    )
    (entry,) = h.world.audit
    assert entry.action == "case.claim" and entry.target == f"case:{CASE}"


async def test_claim_by_other_moderator_is_already_claimed_even_with_stale_version() -> None:
    _, service = _setup()
    await _claim(service)
    with pytest.raises(ConflictError) as err:
        await _claim(service, OTHER_MODERATOR, version=1)
    assert err.value.code == "social.case_already_claimed"


async def test_claim_with_stale_version_conflicts() -> None:
    _, service = _setup()
    with pytest.raises(ConflictError) as err:
        await _claim(service, version=7)
    assert err.value.code == "social.case_version_conflict"


async def test_claim_replay_by_key_returns_same_without_second_audit() -> None:
    h, service = _setup()
    key = _key()
    first = await service.claim(MODERATOR, CASE, 1, key)
    second = await service.claim(MODERATOR, CASE, 1, key)
    assert first == second
    assert len(h.world.audit) == 1


async def test_unknown_case_is_not_found() -> None:
    _, service = _setup()
    with pytest.raises(NotFoundError) as err:
        await service.claim(MODERATOR, UUID(int=1), 1, _key())
    assert err.value.code == "social.case_not_found"


async def test_escalate_needs_case_in_work_by_the_assignee() -> None:
    _, service = _setup()
    with pytest.raises(ConflictError) as wrong:
        await service.escalate(MODERATOR, CASE, 1, REASON, _key())
    assert wrong.value.code == "social.case_wrong_state"
    await _claim(service)
    with pytest.raises(ForbiddenError) as other:
        await service.escalate(OTHER_MODERATOR, CASE, 2, REASON, _key())
    assert other.value.code == "social.case_not_assignee"
    data = await service.escalate(MODERATOR, CASE, 2, REASON, _key())
    assert (data["status"], data["version"]) == ("escalated", 3)


# ------------------------------------------------------------------ resolve


async def test_resolve_dismissed_emits_one_event_per_report_without_texts() -> None:
    h, service = _setup()
    await _claim(service)
    data = await _resolve(service)
    assert (data["status"], data["decision"], data["version"]) == ("resolved", "dismissed", 3)
    (moderated,) = h.events("ContentModerated")
    assert isinstance(moderated, ContentModerated)
    assert moderated.aggregate_id == str(CASE) and moderated.moderator_id == MODERATOR.user_id
    resolved = h.events("ReportResolved")
    assert {e.reporter_id for e in resolved if isinstance(e, ReportResolved)} == set(REPORTERS)
    assert len(resolved) == len(REPORTERS)
    for event in h.world.events:
        assert not {"text", "reason", "note"} & set(event.payload())
    assert h.world.comments[COMMENT].body != ""  # отклонение контент не трогает


async def test_resolve_content_removed_hides_comment_as_moderator() -> None:
    h, service = _setup()
    await _claim(service)
    await _resolve(service, "content_removed")
    comment = h.world.comments[COMMENT]
    assert comment.is_deleted and comment.deleted_role == "moderator"
    assert comment.deleted_by == MODERATOR.user_id and comment.body == ""
    (deleted,) = h.events("CommentDeleted")
    assert isinstance(deleted, CommentDeleted) and deleted.by_role == "moderator"


async def test_resolve_content_removed_for_collection_only_emits_event() -> None:
    h, service = _setup("collection")
    await _claim(service)
    await _resolve(service, "content_removed")
    assert not h.events("CommentDeleted")
    (moderated,) = h.events("ContentModerated")
    assert isinstance(moderated, ContentModerated)
    assert (moderated.target_kind, moderated.decision) == ("collection", "content_removed")


async def test_content_removed_makes_no_sense_for_player() -> None:
    h, service = _setup("player")
    await _claim(service)
    with pytest.raises(InvalidInputError) as err:
        await _resolve(service, "content_removed")
    assert err.value.code == "social.decision_not_applicable"
    assert not h.world.events


async def test_resolved_case_is_final() -> None:
    _, service = _setup()
    await _claim(service)
    await _resolve(service)
    for call in (
        _claim(service, version=3),
        _resolve(service, version=3),
        service.escalate(MODERATOR, CASE, 3, REASON, _key()),
    ):
        with pytest.raises(ConflictError) as err:
            await call
        assert err.value.code == "social.case_resolved"


async def test_resolve_replay_by_key_does_not_duplicate_events() -> None:
    h, service = _setup()
    await _claim(service)
    key = _key()
    kwargs = {"version": 2, "decision": "dismissed", "sanction": None, "reason": REASON}
    first = await service.resolve(MODERATOR, CASE, key=key, **kwargs)  # type: ignore[arg-type]
    second = await service.resolve(MODERATOR, CASE, key=key, **kwargs)  # type: ignore[arg-type]
    assert first == second
    assert len(h.events("ReportResolved")) == len(REPORTERS)


async def test_open_case_must_be_claimed_before_resolving() -> None:
    _, service = _setup()
    with pytest.raises(ConflictError) as err:
        await _resolve(service, version=1)
    assert err.value.code == "social.case_wrong_state"


async def test_only_assignee_resolves_in_work_case() -> None:
    _, service = _setup()
    await _claim(service)
    with pytest.raises(ConflictError) as err:
        await _resolve(service, staff=OTHER_MODERATOR)
    assert err.value.code == "social.case_already_claimed"


async def test_stale_version_blocks_resolve() -> None:
    _, service = _setup()
    await _claim(service)
    with pytest.raises(ConflictError) as err:
        await _resolve(service, version=1)
    assert err.value.code == "social.case_version_conflict"


async def test_escalated_case_is_resolved_only_by_senior() -> None:
    h, service = _setup()
    await _claim(service)
    await service.escalate(MODERATOR, CASE, 2, REASON, _key())
    with pytest.raises(ForbiddenError) as err:
        await _resolve(service, version=3)  # даже исполнитель - не старший
    assert err.value.code == "social.case_escalated_forbidden"
    data = await _resolve(service, staff=ADMIN, version=3)
    assert data["status"] == "resolved" and data["resolved_by"] == str(ADMIN.user_id)
    assert h.events("ContentModerated")


# ------------------------------------------------------------------ санкция в той же транзакции


async def test_sanctioned_calls_granter_for_target_user_with_actor_perms() -> None:
    h, service = _setup()
    await _claim(service)
    spec = SanctionSpec("chat", 3600, "оскорбления")
    await _resolve(service, "sanctioned", sanction=spec)
    (grant,) = h.world.grants
    assert (grant.user_id, grant.kind, grant.duration_s) == (TARGET_USER, "chat", 3600)
    assert grant.actor_id == MODERATOR.user_id and grant.actor_perms == MODERATOR.perms
    assert grant.reason == "оскорбления"


async def test_granter_error_rolls_back_whole_resolution() -> None:
    h, service = _setup()
    await _claim(service)
    events_before = len(h.world.events)
    h.grant_error = ForbiddenError("себе нельзя", code="social.self_sanction_forbidden")
    with pytest.raises(ForbiddenError):
        await _resolve(service, "sanctioned", sanction=SanctionSpec("chat", 3600, "x" * 5))
    assert h.world.cases[CASE]["status"] == "in_work"
    assert h.world.cases[CASE]["version"] == 2
    assert len(h.world.events) == events_before and not h.world.grants
    assert len(h.world.audit) == 1  # только claim


# ------------------------------------------------------------------ чтение


async def test_queue_filters_and_orders_oldest_first() -> None:
    h, service = _setup()
    second = UUID(int=1001)
    h.world.cases[second] = {**h.world.cases[CASE], "kind": "collection", "target": COLLECTION}
    h.clock.advance(seconds=5)
    h.world.cases[second]["opened_at"] = h.clock.now()
    page = await service.queue(
        MODERATOR, status=None, target_kind=None, mine=False, cursor=None, limit=1
    )
    assert [i["id"] for i in page["items"]] == [str(CASE)] and page["next_cursor"]
    nxt = await service.queue(
        MODERATOR, status=None, target_kind=None, mine=False, cursor=page["next_cursor"], limit=5
    )
    assert [i["id"] for i in nxt["items"]] == [str(second)] and nxt["next_cursor"] is None
    only_collections = await service.queue(
        MODERATOR, status="open", target_kind="collection", mine=False, cursor=None, limit=5
    )
    assert [i["id"] for i in only_collections["items"]] == [str(second)]
    await _claim(service)
    mine = await service.queue(
        MODERATOR, status=None, target_kind=None, mine=True, cursor=None, limit=5
    )
    assert [i["id"] for i in mine["items"]] == [str(CASE)]
    others = await service.queue(
        OTHER_MODERATOR, status=None, target_kind=None, mine=True, cursor=None, limit=5
    )
    assert others["items"] == []


async def test_detail_has_report_texts_and_preview() -> None:
    _, service = _setup()
    data = await service.detail(CASE)
    assert data["target_preview"] == "грубый текст"
    assert {r["text"] for r in data["reports"]} == {f"секретный текст жалобы {i}" for i in range(3)}
    assert data["reports_count"] == 3
