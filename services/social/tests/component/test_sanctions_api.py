"""Санкции через HTTP на реальной БД: права, выдача, замена, снятие, истечение, события."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from social_service.infrastructure.container import container_for
from social_service.infrastructure.db import tables_moderation as m
from social_service.infrastructure.db import tables_sanctions as t

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_testkit import assert_problem

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.component

MANAGE = ("admin.access", "social.sanctions.manage", "social.moderate")
ACCOUNT = (*MANAGE, "social.sanctions.account")
HOUR2 = 7200
REASON = "нарушение правил"


async def _grant(  # noqa: PLR0917 - тестовый помощник
    s: SocialService,
    moderator: UUID,
    player: UUID,
    kind: str = "online",
    duration_s: int | None = HOUR2,
    perms: tuple[str, ...] = MANAGE,
    key: dict[str, str] | None = None,
) -> Any:
    return await s.client.post(
        f"/admin/v1/users/{player}/sanctions",
        json={"kind": kind, "duration_s": duration_s, "reason": REASON},
        headers={**s.auth(moderator, perms), **(key or s.key())},
    )


async def _player(s: SocialService) -> UUID:
    user = uuid7()
    await s.add_nickname(user, f"p{user.hex[-10:]}")
    return user


async def test_endpoints_require_authentication_and_rights(social: SocialService) -> None:
    user = uuid7()
    calls = [
        ("GET", f"/admin/v1/users/{user}/moderation", None),
        ("POST", f"/admin/v1/users/{user}/sanctions", {"kind": "online", "reason": REASON}),
        ("POST", f"/admin/v1/sanctions/{user}/revoke", {"reason": REASON}),
    ]
    for method, path, body in calls:
        anon = await social.client.request(method, path, json=body, headers=social.key())
        assert_problem(anon, 401, "auth.unauthenticated")
        plain = await social.client.request(
            method, path, json=body, headers={**social.auth(), **social.key()}
        )
        assert_problem(plain, 403, "auth.forbidden")
    # Модератору очереди без права на санкции выдача закрыта.
    only_queue = social.auth(perms=("admin.access", "social.moderate"))
    denied = await social.client.post(
        f"/admin/v1/users/{user}/sanctions",
        json={"kind": "online", "reason": REASON},
        headers={**only_queue, **social.key()},
    )
    assert_problem(denied, 403, "auth.forbidden")
    assert_problem(await social.client.get("/v1/me/sanctions"), 401, "auth.unauthenticated")
    guest = await social.client.get("/v1/me/sanctions", headers=social.guest())
    assert guest.status_code == 403


async def test_grant_returns_view_and_publishes_event(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    response = await _grant(social, moderator, player, "chat")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["kind"], body["active"], body["actor_id"]) == ("chat", True, str(moderator))
    assert body["revoked_at"] is None
    [event] = await social.outbox_events("social.UserSanctionsChanged")
    payload = event.payload
    assert event.aggregate_id == str(player)
    assert payload["mask"] == int(Sanction.CHAT_MUTE | Sanction.COMMENTS)
    assert payload["version"] == 1
    assert payload["changed_by"] == str(moderator)
    assert payload["account_banned"] is False
    assert "reason" not in str(payload)


async def test_grant_is_idempotent_by_key(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    key = social.key()
    first = await _grant(social, moderator, player, key=key)
    again = await _grant(social, moderator, player, key=key)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert await social.count(t.sanctions) == 1
    assert len(await social.outbox_events("social.UserSanctionsChanged")) == 1
    other = await _grant(social, moderator, player, kind="ranked", key=key)
    assert_problem(other, 409, "idempotency.key_reused")


async def test_validation_and_domain_errors(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    for body in (
        {"kind": "ban", "duration_s": HOUR2, "reason": REASON},
        {"kind": "online", "duration_s": 1, "reason": REASON},
        {"kind": "online", "duration_s": HOUR2},
    ):
        response = await social.client.post(
            f"/admin/v1/users/{player}/sanctions",
            json=body,
            headers={**social.auth(moderator, MANAGE), **social.key()},
        )
        assert response.status_code == 422
    # Секунд достаточно для схемы API, но меньше минимума конфига.
    below = await _grant(social, moderator, player, duration_s=120)
    assert_problem(below, 422, "social.sanction_duration_invalid")
    assert_problem(await _grant(social, moderator, uuid7()), 404, "social.player_not_found")
    assert_problem(
        await _grant(social, moderator, moderator), 403, "social.self_sanction_forbidden"
    )
    assert await social.count(t.sanctions) == 0


async def test_account_sanction_needs_separate_right(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    denied = await _grant(social, moderator, player, "account", None)
    assert_problem(denied, 403, "social.account_sanction_forbidden")
    granted = await _grant(social, moderator, player, "account", None, perms=ACCOUNT)
    assert granted.status_code == 201
    [event] = await social.outbox_events("social.UserSanctionsChanged")
    assert event.payload["account_banned"] is True
    assert event.payload["mask"] == 0


async def test_same_kind_replaces_previous(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    first = (await _grant(social, moderator, player)).json()
    second = (await _grant(social, moderator, player, duration_s=2 * HOUR2)).json()
    rows = {r.id: r for r in await social.rows(t.sanctions)}
    assert rows[UUID(first["id"])].revoke_reason == "replaced"
    assert rows[UUID(second["id"])].revoked_at is None
    mine = await social.client.get("/v1/me/sanctions", headers=social.auth(player))
    assert [i["kind"] for i in mine.json()["items"]] == ["online"]
    versions = [
        e.payload["version"] for e in await social.outbox_events("social.UserSanctionsChanged")
    ]
    assert sorted(versions) == [1, 2]


async def test_concurrent_grants_keep_versions_monotonic_and_one_open(
    social: SocialService,
) -> None:
    moderator, player = uuid7(), await _player(social)
    results = await asyncio.gather(*(_grant(social, moderator, player) for _ in range(4)))
    assert [r.status_code for r in results] == [201] * 4
    open_rows = [r for r in await social.rows(t.sanctions) if r.revoked_at is None]
    assert len(open_rows) == 1
    versions = [
        e.payload["version"] for e in await social.outbox_events("social.UserSanctionsChanged")
    ]
    assert sorted(versions) == [1, 2, 3, 4]


async def test_revoke_is_idempotent_and_unknown_is_404(social: SocialService) -> None:
    moderator, other, player = uuid7(), uuid7(), await _player(social)
    sanction = (await _grant(social, moderator, player)).json()
    path = f"/admin/v1/sanctions/{sanction['id']}/revoke"
    headers = {**social.auth(other, MANAGE), **social.key()}
    first = await social.client.post(path, json={"reason": "ошибка"}, headers=headers)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert (first.json()["active"], first.json()["revoked_by"]) == (False, str(other))
    again = await social.client.post(path, json={"reason": "ещё"}, headers=headers)
    assert again.json() == first.json()
    events = await social.outbox_events("social.UserSanctionsChanged")
    assert len(events) == 2
    assert max(events, key=lambda e: e.payload["version"]).payload["mask"] == 0
    missing = await social.client.post(
        f"/admin/v1/sanctions/{uuid7()}/revoke", json={"reason": "нет"}, headers=headers
    )
    assert_problem(missing, 404, "social.sanction_not_found")


async def test_moderator_cannot_revoke_own_sanction(social: SocialService) -> None:
    admin, moderator = uuid7(), await _player(social)
    sanction = (await _grant(social, admin, moderator)).json()
    response = await social.client.post(
        f"/admin/v1/sanctions/{sanction['id']}/revoke",
        json={"reason": "сам"},
        headers={**social.auth(moderator, MANAGE), **social.key()},
    )
    assert_problem(response, 403, "social.self_sanction_forbidden")


async def test_my_sanctions_show_only_active_own_with_reason(social: SocialService) -> None:
    moderator, player, other = uuid7(), await _player(social), await _player(social)
    await _grant(social, moderator, player, "ranked", None)
    await _grant(social, moderator, other, "chat")
    mine = await social.client.get("/v1/me/sanctions", headers=social.auth(player))
    assert mine.status_code == 200
    assert mine.headers["cache-control"] == "no-store"
    [item] = mine.json()["items"]
    assert (item["kind"], item["expires_at"], item["reason"]) == ("ranked", None, REASON)
    social.clock.advance(timedelta(days=3650))
    later = await social.client.get("/v1/me/sanctions", headers=social.auth(other))
    assert later.json()["items"] == []


async def test_expiry_job_closes_and_publishes_with_null_actor(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    await _grant(social, moderator, player, "online", HOUR2)
    await _grant(social, moderator, player, "ranked", None)
    sanctions = container_for(social.infra).sanctions()
    assert await sanctions.expire_due() == 0
    social.clock.advance(timedelta(seconds=HOUR2 + 1))
    assert await sanctions.expire_due() == 1
    assert await sanctions.expire_due() == 0
    events = sorted(
        await social.outbox_events("social.UserSanctionsChanged"),
        key=lambda e: e.payload["version"],
    )
    last = events[-1].payload
    assert (last["version"], last["changed_by"], last["mask"]) == (3, None, int(Sanction.RANKED))
    expired = [r for r in await social.rows(t.sanctions) if r.kind == "online"]
    assert expired[0].revoke_reason == "expired"
    assert expired[0].revoked_by is None


async def test_moderation_card_has_counters_history_and_cases(social: SocialService) -> None:
    moderator, player = uuid7(), await _player(social)
    await _grant(social, moderator, player, "chat")
    async with social.infra.sessions() as session, session.begin():  # type: ignore[misc]
        await session.execute(m.behaviour_counters.insert().values(user_id=player, forfeits=2))
        await session.execute(
            m.moderation_cases.insert().values(
                id=uuid7(),
                target_kind="player",
                target_id=player,
                target_user_id=player,
                status="open",
                opened_at=social.clock.now(),
                version=1,
            )
        )
    response = await social.client.get(
        f"/admin/v1/users/{player}/moderation", headers=social.auth(moderator, MANAGE)
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    card = response.json()
    assert card["counters"]["forfeits"] == 2
    assert card["counters"]["praises"] == 0
    assert [s["kind"] for s in card["sanctions"]] == ["chat"]
    assert [c["status"] for c in card["cases"]] == ["open"]
    unknown = await social.client.get(
        f"/admin/v1/users/{uuid7()}/moderation", headers=social.auth(moderator, MANAGE)
    )
    assert_problem(unknown, 404, "social.player_not_found")


async def test_granter_port_joins_open_transaction(social: SocialService) -> None:
    """F4 решает кейс и выдаёт санкцию в одной транзакции: откат уносит и санкцию, и событие."""
    from social_service.application.ports.sanctions import GrantRequest

    moderator, player = uuid7(), await _player(social)
    request = GrantRequest(moderator, frozenset(MANAGE), player, "online", HOUR2, REASON)
    assert social.infra.sessions is not None
    async with social.infra.sessions() as session:
        outer = await session.begin()
        record = await container_for(social.infra).sanctions(session).grant(request)
        assert await container_for(social.infra).sanctions(session).active_mask(player) == int(
            Sanction.ONLINE
        )
        assert record.kind == "online"
        await outer.rollback()
    assert await social.count(t.sanctions) == 0
    assert await social.outbox_events("social.UserSanctionsChanged") == []
    async with social.infra.sessions() as session, session.begin():
        await container_for(social.infra).sanctions(session).grant(request)
    assert await social.count(t.sanctions) == 1
    assert len(await social.outbox_events("social.UserSanctionsChanged")) == 1
