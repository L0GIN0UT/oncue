"""Сквозные сценарии модерации на настоящем контейнере social: жалобы, кейс, санкция, события."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from social_service.infrastructure.db import tables_moderation as m
from social_service.infrastructure.db import tables_sanctions as s

from gv_auth import Sanction
from gv_core.errors import ForbiddenError
from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_problem, load_example

if TYPE_CHECKING:
    from conftest import Real

pytestmark = pytest.mark.component

MODERATE = ("admin.access", "social.moderate")
MANAGE = (*MODERATE, "social.sanctions.manage")
SENIOR = (*MANAGE, "social.sanctions.account")
SECRET = "уникальный-текст-жалобы-123"
REASON_SECRET = "уникальная-причина-решения-456"
SANCTION_REASON = "нарушение-правил-789"
EVENT_TYPES = ("social.UserSanctionsChanged", "social.ContentModerated", "social.ReportResolved")


class Flow:
    """Комментарий нарушителя, жалобы до порога из конфига, кейс."""

    def __init__(self, real: Real) -> None:
        self.r = real
        self.moderator = uuid7()
        self.offender = uuid7()
        self.reporters: list[UUID] = []
        self.case: UUID
        self.comment: UUID

    def mod(self, perms: tuple[str, ...] = MANAGE, user: UUID | None = None) -> dict[str, str]:
        return self.r.auth(user or self.moderator, perms)

    async def open_case(self) -> UUID:
        r = self.r
        await r.add_nickname(self.offender)
        collection = await r.publish_collection(uuid7())
        posted = await r.client.post(
            f"/v1/collections/{collection}/comments",
            json={"body": "грубый комментарий"},
            headers={**r.auth(self.offender), **r.key()},
        )
        assert posted.status_code == 201, posted.text
        self.comment = UUID(posted.json()["id"])
        threshold = (await r.social.config.current()).reports.threshold_per_day
        for i in range(threshold):
            assert not await r.rows(m.moderation_cases), f"кейс открылся раньше порога ({i})"
            reporter = uuid7()
            self.reporters.append(reporter)
            filed = await r.client.post(
                "/v1/reports",
                json={
                    "target_kind": "comment",
                    "target_id": str(self.comment),
                    "reason_code": "spam",
                    "text": SECRET,
                },
                headers={**r.auth(reporter), **r.key()},
            )
            assert filed.status_code == 201, filed.text
        (case,) = await r.rows(m.moderation_cases)
        self.case = case.id
        return case.id

    async def open_and_claim(self, headers: dict[str, str] | None = None) -> None:
        await self.open_case()
        claimed = await self.act("claim", {"version": 1}, headers or self.mod())
        assert claimed.status_code == 200, claimed.text

    def url(self, suffix: str = "") -> str:
        return f"/admin/v1/cases/{self.case}{suffix}"

    async def act(self, action: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
        return await self.r.client.post(
            self.url(f"/{action}"), json=body, headers={**headers, **self.r.key()}
        )

    @staticmethod
    def sanction_body(version: int = 2, **sanction: Any) -> dict[str, Any]:
        grant = {"kind": "online", "duration_s": 3600, "reason": SANCTION_REASON} | sanction
        return {
            "version": version,
            "decision": "sanctioned",
            "sanction": grant,
            "reason": REASON_SECRET,
        }


async def test_reports_threshold_claim_resolve_sanctioned_is_atomic(real: Real) -> None:
    flow = Flow(real)
    await flow.open_and_claim()
    assert len(flow.reporters) == 3  # порог берётся из конфига, а не из кода

    resolved = await flow.act("resolve", flow.sanction_body(), flow.mod())
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved" and resolved.json()["decision"] == "sanctioned"

    (case,) = await real.rows(m.moderation_cases)
    assert case.status == "resolved"
    (record,) = await real.rows(s.sanctions)
    assert record.user_id == flow.offender and record.kind == "online"
    assert (record.expires_at - record.started_at).total_seconds() == 3600

    (changed,) = await real.outbox("social.UserSanctionsChanged")
    assert changed.payload["user_id"] == str(flow.offender)
    assert changed.payload["mask"] == int(Sanction.ONLINE) == 32
    assert changed.payload["version"] == 1
    assert changed.payload["account_banned"] is False
    (moderated,) = await real.outbox("social.ContentModerated")
    assert moderated.payload["decision"] == "sanctioned"
    reports = await real.outbox("social.ReportResolved")
    assert sorted(e.payload["reporter_id"] for e in reports) == sorted(
        str(x) for x in flow.reporters
    )
    for event in (changed, moderated, *reports):
        raw = event.to_bytes().decode()
        for private in (SECRET, REASON_SECRET, SANCTION_REASON):
            assert private not in raw
        assert not {"text", "reason", "note"} & set(event.payload)


async def test_failure_after_real_grant_rolls_back_case_sanction_and_events(real: Real) -> None:
    flow = Flow(real)
    await flow.open_and_claim()
    events_before = len(await real.outbox())

    class FailingAfterGrant:
        """Настоящая выдача в транзакции кейса, затем сбой."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def grant(self, request: Any) -> Any:
            await self._inner.grant(request)
            raise ForbiddenError("сбой после выдачи", code="social.test_failure")

    real.grant_wrapper = FailingAfterGrant

    failed = await flow.act("resolve", flow.sanction_body(), flow.mod())
    assert_problem(failed, 403, "social.test_failure")

    (case,) = await real.rows(m.moderation_cases)
    assert (case.status, case.version) == ("in_work", 2)
    assert not await real.rows(s.sanctions)
    for event_type in EVENT_TYPES:
        assert not await real.outbox(event_type)
    assert len(await real.outbox()) == events_before


@pytest.mark.parametrize("duration", [60, 31_536_001])
async def test_duration_out_of_config_bounds_rolls_back_case(real: Real, duration: int) -> None:
    flow = Flow(real)
    await flow.open_and_claim()
    response = await flow.act("resolve", flow.sanction_body(duration_s=duration), flow.mod())
    assert response.status_code == 422, response.text
    (case,) = await real.rows(m.moderation_cases)
    assert case.status == "in_work"
    assert not await real.rows(s.sanctions)
    for event_type in EVENT_TYPES:
        assert not await real.outbox(event_type)


async def test_limits_come_from_config_version(real: Real) -> None:
    await real.publish_config(version=2, sanctions__min_duration_s=60)
    flow = Flow(real)
    await flow.open_and_claim()
    ok = await flow.act("resolve", flow.sanction_body(duration_s=60), flow.mod())
    assert ok.status_code == 200, ok.text
    (record,) = await real.rows(s.sanctions)
    assert (record.expires_at - record.started_at).total_seconds() == 60


async def test_resolve_replay_same_key_same_response_other_body_conflicts(real: Real) -> None:
    flow = Flow(real)
    await flow.open_and_claim()
    headers = {**flow.mod(), **real.key()}
    body = flow.sanction_body()
    first = await real.client.post(flow.url("/resolve"), json=body, headers=headers)
    assert first.status_code == 200, first.text
    counts = {t: len(await real.outbox(t)) for t in EVENT_TYPES}
    assert all(counts.values())

    replay = await real.client.post(flow.url("/resolve"), json=body, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    other = await real.client.post(
        flow.url("/resolve"), json={**body, "reason": "другое решение"}, headers=headers
    )
    assert other.status_code == 409, other.text

    assert len(await real.rows(s.sanctions)) == 1
    assert {t: len(await real.outbox(t)) for t in EVENT_TYPES} == counts


async def test_account_sanction_requires_account_permission(real: Real) -> None:
    flow = Flow(real)
    await flow.open_and_claim()
    body = flow.sanction_body(kind="account", duration_s=None)
    denied = await flow.act("resolve", body, flow.mod(MANAGE))
    assert_problem(denied, 403, "social.account_sanction_forbidden")
    (case,) = await real.rows(m.moderation_cases)
    assert case.status == "in_work"
    assert not await real.rows(s.sanctions)

    ok = await flow.act("resolve", body, flow.mod(SENIOR))
    assert ok.status_code == 200, ok.text
    (changed,) = await real.outbox("social.UserSanctionsChanged")
    assert changed.payload["account_banned"] is True


async def test_self_sanction_is_forbidden(real: Real) -> None:
    flow = Flow(real)
    await flow.open_case()
    me = flow.mod(MANAGE, user=flow.offender)  # модератор - сам нарушитель
    claimed = await flow.act("claim", {"version": 1}, me)
    if claimed.status_code == 200:
        denied = await flow.act("resolve", flow.sanction_body(), me)
        assert_problem(denied, 403, "social.self_sanction_forbidden")
        (case,) = await real.rows(m.moderation_cases)
        assert case.status == "in_work"
    else:
        assert claimed.status_code == 403, claimed.text
    assert not await real.rows(s.sanctions)
    assert not await real.outbox("social.UserSanctionsChanged")


async def test_chat_sanction_mutes_comments_until_revoked(real: Real) -> None:
    player, moderator = uuid7(), uuid7()
    await real.add_nickname(player)
    collection = await real.publish_collection(uuid7())
    url = f"/v1/collections/{collection}/comments"

    async def post() -> Any:
        return await real.client.post(
            url, json={"body": "Привет"}, headers={**real.auth(player), **real.key()}
        )

    assert (await post()).status_code == 201
    granted = await real.client.post(
        f"/admin/v1/users/{player}/sanctions",
        json={"kind": "chat", "duration_s": 3600, "reason": "мат"},
        headers={**real.auth(moderator, MANAGE), **real.key()},
    )
    assert granted.status_code == 201, granted.text
    assert_problem(await post(), 403, "social.chat_muted")  # claim чистый, мьют - из таблицы

    revoked = await real.client.post(
        f"/admin/v1/sanctions/{granted.json()['id']}/revoke",
        json={"reason": "прощён"},
        headers={**real.auth(moderator, MANAGE), **real.key()},
    )
    assert revoked.status_code == 200, revoked.text
    assert (await post()).status_code == 201


async def _roles_changed(real: Real, user: UUID, perms: tuple[str, ...], version: int) -> None:
    base = load_example("identity.UserRolesChanged")
    payload = {**base.payload, "user_id": str(user), "perms": sorted(perms), "version": version}
    outcome = await real.deliver(base.model_copy(update={"payload": payload, "event_id": uuid7()}))
    assert outcome is Outcome.PROCESSED


async def test_moderator_cannot_sanction_admin_but_admin_can(real: Real) -> None:
    admin_target = uuid7()
    await real.add_nickname(admin_target)
    await _roles_changed(real, admin_target, (*SENIOR, "admin.users.manage"), 1)

    async def grant(actor_perms: tuple[str, ...]) -> Any:
        return await real.client.post(
            f"/admin/v1/users/{admin_target}/sanctions",
            json={"kind": "chat", "duration_s": 3600, "reason": "нарушение"},
            headers={**real.auth(uuid7(), actor_perms), **real.key()},
        )

    assert_problem(await grant(MANAGE), 403, "social.sanction_target_outranks")
    assert not await real.rows(s.sanctions)
    assert not await real.outbox("social.UserSanctionsChanged")

    ok = await grant((*SENIOR, "admin.users.manage"))
    assert ok.status_code == 201, ok.text

    # Права понижены новым снимком - модератор снова может; старое событие не откатывает.
    await _roles_changed(real, admin_target, (), 2)
    await _roles_changed(real, admin_target, (*SENIOR, "admin.users.manage"), 1)
    assert (await grant(MANAGE)).status_code == 201


async def test_case_resolution_against_admin_is_refused(real: Real) -> None:
    flow = Flow(real)
    await flow.open_and_claim()
    await _roles_changed(real, flow.offender, (*SENIOR, "admin.users.manage"), 1)
    denied = await flow.act("resolve", flow.sanction_body(), flow.mod(MANAGE))
    assert_problem(denied, 403, "social.sanction_target_outranks")
    (case,) = await real.rows(m.moderation_cases)
    assert case.status == "in_work"
    assert not await real.rows(s.sanctions)
