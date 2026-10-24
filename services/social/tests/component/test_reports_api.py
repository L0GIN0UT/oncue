"""Жалобы через HTTP: справочник, проверки, идемпотентность, кейс по порогу, события."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from social_fakes import example_config
from social_service.infrastructure.db import tables_moderation as t

from gv_core.ids import uuid7
from gv_testkit import assert_problem

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.component


async def _file(s: SocialService, user: UUID, body: dict[str, Any]) -> Any:
    return await s.client.post("/v1/reports", json=body, headers={**s.auth(user), **s.key()})


def _player(
    target: UUID, match: UUID | None, reason: str = "cheating", **extra: Any
) -> dict[str, Any]:
    return {
        "target_kind": "player",
        "target_id": str(target),
        "context_match_id": str(match) if match else None,
        "reason_code": reason,
        **extra,
    }


async def test_reasons_are_public_and_localized(social: SocialService) -> None:
    response = await social.client.get("/v1/report-reasons")
    assert response.status_code == 200
    items = {i["code"]: i for i in response.json()["items"]}
    assert items["cheating"]["title"] == "Читерство или договорная игра"
    assert items["spam"]["target_kinds"] == ["collection", "comment"]


async def test_reports_need_account(social: SocialService) -> None:
    body = {"json": _player(uuid7(), uuid7()), "headers": social.key()}
    assert_problem(await social.client.post("/v1/reports", **body), 401, "auth.unauthenticated")
    guest = await social.client.post(
        "/v1/reports", json=body["json"], headers={**social.guest(), **social.key()}
    )
    assert guest.status_code == 403


async def test_player_report_needs_shared_match_then_is_idempotent(social: SocialService) -> None:
    author, target, match = uuid7(), uuid7(), uuid7()
    no_context = await _file(social, author, _player(target, None))
    assert_problem(no_context, 422, "social.report_no_shared_match")
    stranger_match = await _file(social, author, _player(target, match))
    assert_problem(stranger_match, 422, "social.report_no_shared_match")
    await social.add_participants(match, author, target)

    first = await _file(social, author, _player(target, match, text="Мешал играть"))
    assert first.status_code == 201
    assert first.json()["repeated"] is False
    again = await _file(social, author, _player(target, match))
    assert again.status_code == 200
    assert again.json() == {"id": first.json()["id"], "repeated": True}

    assert await social.count(t.reports) == 1
    (row,) = await social.rows(t.reports)
    assert row.text == "Мешал играть" and row.target_user_id == target
    (event,) = await social.outbox_events("social.ReportFiled")
    assert event.payload["report_id"] == first.json()["id"]
    assert "text" not in event.payload and event.payload["case_id"] is None
    (counters,) = await social.rows(t.behaviour_counters, t.behaviour_counters.c.user_id == target)
    assert counters.reports_received == 1


async def test_solo_match_is_not_shared(social: SocialService) -> None:
    author, target, match = uuid7(), uuid7(), uuid7()
    await social.add_participants(match, author, target, solo=True)
    assert_problem(
        await _file(social, author, _player(target, match)), 422, "social.report_no_shared_match"
    )


async def test_cannot_report_self_or_unknown_reason(social: SocialService) -> None:
    user, match = uuid7(), uuid7()
    assert_problem(await _file(social, user, _player(user, match)), 422, "social.report_self")
    other = await _file(social, user, _player(uuid7(), match, reason="nope"))
    assert_problem(other, 422, "social.report_reason_unknown")
    wrong_kind = await _file(
        social,
        user,
        {"target_kind": "collection", "target_id": str(uuid7()), "reason_code": "cheating"},
    )
    assert_problem(wrong_kind, 422, "social.report_reason_unknown")


async def test_text_limit_from_config(social: SocialService) -> None:
    social.config.config = example_config(reports={"text_max_length": 5})
    response = await _file(
        social,
        uuid7(),
        {
            "target_kind": "collection",
            "target_id": str(uuid7()),
            "reason_code": "spam",
            "text": "слишком длинно",
        },
    )
    assert_problem(response, 422, "social.report_text_too_long")


async def test_collection_and_comment_reports(social: SocialService) -> None:
    owner, reporter = uuid7(), uuid7()
    collection = await social.publish_collection(owner)
    comment = await social.client.post(
        f"/v1/collections/{collection}/comments",
        json={"body": "Привет"},
        headers={**social.auth(owner), **social.key()},
    )
    ok = await _file(
        social,
        reporter,
        {"target_kind": "collection", "target_id": str(collection), "reason_code": "spam"},
    )
    assert ok.status_code == 201
    ok2 = await _file(
        social,
        reporter,
        {"target_kind": "comment", "target_id": comment.json()["id"], "reason_code": "spam"},
    )
    assert ok2.status_code == 201
    missing = await _file(
        social,
        reporter,
        {"target_kind": "comment", "target_id": str(uuid7()), "reason_code": "spam"},
    )
    assert_problem(missing, 404, "social.comment_not_found")
    missing_c = await _file(
        social,
        reporter,
        {"target_kind": "collection", "target_id": str(uuid7()), "reason_code": "spam"},
    )
    assert_problem(missing_c, 404, "social.collection_not_found")
    own = await _file(
        social,
        owner,
        {"target_kind": "collection", "target_id": str(collection), "reason_code": "spam"},
    )
    assert_problem(own, 422, "social.report_self")


async def test_avatar_reports_are_not_implemented(social: SocialService) -> None:
    response = await _file(
        social,
        uuid7(),
        {"target_kind": "avatar", "target_id": str(uuid7()), "reason_code": "other"},
    )
    assert_problem(response, 501, "common.not_implemented")


async def test_daily_limit_is_429(social: SocialService) -> None:
    user = uuid7()
    limit = social.config.config.reports.per_day
    for _ in range(limit):
        collection = await social.publish_collection(uuid7())
        ok = await _file(
            social,
            user,
            {"target_kind": "collection", "target_id": str(collection), "reason_code": "spam"},
        )
        assert ok.status_code == 201
    collection = await social.publish_collection(uuid7())
    over = await _file(
        social,
        user,
        {"target_kind": "collection", "target_id": str(collection), "reason_code": "spam"},
    )
    assert_problem(over, 429, "social.reports_rate_limit")
    assert await social.count(t.reports) == limit


async def test_threshold_opens_one_case_and_later_reports_join(social: SocialService) -> None:
    owner = uuid7()
    collection = await social.publish_collection(owner)
    threshold = social.config.config.reports.threshold_per_day
    body = {"target_kind": "collection", "target_id": str(collection), "reason_code": "spam"}
    for _ in range(threshold + 1):
        assert (await _file(social, uuid7(), body)).status_code == 201
    (case,) = await social.rows(t.moderation_cases)
    assert case.status == "open" and case.target_user_id == owner
    assert await social.count(t.case_reports) == threshold + 1
    events = await social.outbox_events("social.ReportFiled")
    with_case = [e for e in events if e.payload["case_id"] is not None]
    assert len(with_case) == 2 and {e.payload["case_id"] for e in with_case} == {str(case.id)}
