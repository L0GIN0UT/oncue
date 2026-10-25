"""Санкции на двойниках портов: маска, пределы, самосанкция, замена, снятие, истечение, события."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from sanction_fakes import FakeSanctionsUow, SanctionWorld
from social_fakes import FakeConfig, example_config
from social_service.application.ports.sanctions import (
    GrantRequest,
    SanctionKind,
    SanctionReader,
)
from social_service.application.sanctions import SanctionService
from social_service.domain.sanctions import UserSanctionsChanged

from gv_auth import Sanction
from gv_core.errors import ForbiddenError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

MODERATOR = UUID(int=1)
PLAYER = UUID(int=2)
MANAGE = frozenset({"social.sanctions.manage"})
ACCOUNT = frozenset({"social.sanctions.account", "social.sanctions.manage"})
HOUR = 3600


class Env:
    def __init__(self) -> None:
        self.world = SanctionWorld()
        self.world.players.add(PLAYER)
        self.clock = FakeClock()
        self.config = FakeConfig(example_config(sanctions={"min_duration_s": 60}))
        self.service = SanctionService(
            lambda: FakeSanctionsUow(self.world), self.config, self.clock, uuid7
        )

    def request(
        self,
        kind: SanctionKind = "online",
        duration_s: int | None = HOUR,
        *,
        user: UUID = PLAYER,
        actor: UUID = MODERATOR,
        perms: frozenset[str] = MANAGE,
    ) -> GrantRequest:
        return GrantRequest(
            actor_id=actor,
            actor_perms=perms,
            user_id=user,
            kind=kind,
            duration_s=duration_s,
            reason="нарушение правил",
        )

    def last_event(self) -> UserSanctionsChanged:
        event = self.world.events[-1]
        assert isinstance(event, UserSanctionsChanged)
        return event


def test_service_implements_the_reader_port() -> None:
    reader: SanctionReader = Env().service
    assert reader is not None


@pytest.mark.parametrize(
    ("kind", "mask"),
    [
        ("ranked", Sanction.RANKED),
        ("chat", Sanction.CHAT_MUTE | Sanction.COMMENTS),
        ("online", Sanction.ONLINE),
        ("account", Sanction.NONE),
    ],
)
async def test_mask_per_kind(kind: SanctionKind, mask: Sanction) -> None:
    env = Env()
    await env.service.grant(env.request(kind, perms=ACCOUNT))
    assert await env.service.active_mask(PLAYER) == int(mask)
    assert env.last_event().mask == int(mask)
    assert env.last_event().account_banned is (kind == "account")


async def test_masks_combine_and_event_carries_full_state() -> None:
    env = Env()
    await env.service.grant(env.request("ranked", None))
    await env.service.grant(env.request("online", HOUR))
    event = env.last_event()
    assert event.mask == int(Sanction.RANKED | Sanction.ONLINE)
    assert {a.kind for a in event.active} == {"ranked", "online"}
    assert event.version == 2
    assert event.changed_by == MODERATOR
    assert event.next_expiry_at == env.clock.now() + timedelta(hours=1)
    assert event.aggregate_id == str(PLAYER)


async def test_permanent_has_no_expiry() -> None:
    env = Env()
    record = await env.service.grant(env.request("chat", None))
    assert record.expires_at is None
    assert env.last_event().next_expiry_at is None


async def test_moderator_cannot_sanction_self() -> None:
    env = Env()
    with pytest.raises(ForbiddenError) as err:
        await env.service.grant(env.request(user=MODERATOR))
    assert err.value.code == "social.self_sanction_forbidden"
    assert env.world.events == []


ADMIN_PERMS = frozenset(
    {"social.sanctions.manage", "social.sanctions.account", "admin.users.manage"}
)
OTHER_ADMIN = UUID(int=3)
OTHER_MODERATOR = UUID(int=4)


async def test_moderator_cannot_sanction_admin() -> None:
    env = Env()
    env.world.staff_perms[OTHER_ADMIN] = ADMIN_PERMS
    with pytest.raises(ForbiddenError) as err:
        await env.service.grant(env.request(user=OTHER_ADMIN))
    assert err.value.code == "social.sanction_target_outranks"
    assert env.world.records == {} and env.world.events == []


async def test_admin_can_sanction_admin_and_moderator() -> None:
    env = Env()
    env.world.staff_perms[OTHER_ADMIN] = ADMIN_PERMS
    env.world.staff_perms[OTHER_MODERATOR] = MANAGE
    await env.service.grant(env.request(user=OTHER_ADMIN, perms=ADMIN_PERMS))
    await env.service.grant(env.request(user=OTHER_MODERATOR, perms=ADMIN_PERMS))
    assert len(env.world.records) == 2


async def test_moderator_can_sanction_equal_moderator_and_plain_player() -> None:
    env = Env()
    env.world.staff_perms[OTHER_MODERATOR] = MANAGE
    await env.service.grant(env.request(user=OTHER_MODERATOR))
    await env.service.grant(env.request(user=PLAYER))  # записи в проекции нет - прав нет
    assert len(env.world.records) == 2


async def test_account_needs_its_own_permission() -> None:
    env = Env()
    with pytest.raises(ForbiddenError) as err:
        await env.service.grant(env.request("account", perms=MANAGE))
    assert err.value.code == "social.account_sanction_forbidden"
    assert env.world.records == {}
    await env.service.grant(env.request("account", perms=ACCOUNT))
    assert env.last_event().account_banned is True


@pytest.mark.parametrize("duration", [1, 59, 10**9])
async def test_duration_outside_config_limits(duration: int) -> None:
    env = Env()
    env.config.config = example_config(sanctions={"min_duration_s": 60, "max_duration_s": 3600})
    with pytest.raises(InvalidInputError) as err:
        await env.service.grant(env.request(duration_s=duration))
    assert err.value.code == "social.sanction_duration_invalid"


async def test_duration_limits_come_from_config_not_code() -> None:
    env = Env()
    env.config.config = example_config(sanctions={"min_duration_s": 10, "max_duration_s": 20})
    record = await env.service.grant(env.request(duration_s=15))
    assert record.expires_at == env.clock.now() + timedelta(seconds=15)


async def test_same_kind_replaces_without_duplicates() -> None:
    env = Env()
    first = await env.service.grant(env.request("online", HOUR))
    env.clock.advance(timedelta(minutes=10))
    second = await env.service.grant(env.request("online", 2 * HOUR))
    active = await env.service.my_active(PLAYER)
    assert [r.id for r in active] == [second.id]
    closed = env.world.records[first.id]
    assert closed.revoke_reason == "system:replaced"
    assert closed.revoked_by == MODERATOR
    assert second.expires_at == env.clock.now() + timedelta(hours=2)
    assert len(env.last_event().active) == 1


async def test_replacing_expired_unclosed_record_is_marked_expired() -> None:
    env = Env()
    first = await env.service.grant(env.request("online", 60))
    env.clock.advance(timedelta(minutes=5))
    await env.service.grant(env.request("online", HOUR))
    assert env.world.records[first.id].revoke_reason == "system:expired"
    assert env.world.records[first.id].revoked_by is None


async def test_versions_are_monotonic_per_player() -> None:
    env = Env()
    other = UUID(int=3)
    await env.service.grant(env.request("ranked"))
    await env.service.grant(env.request("chat"))
    await env.service.grant(env.request("ranked", user=other))
    versions = [
        (e.user_id, e.version) for e in env.world.events if isinstance(e, UserSanctionsChanged)
    ]
    assert versions == [(PLAYER, 1), (PLAYER, 2), (other, 1)]


async def test_revoke_closes_and_publishes_once() -> None:
    env = Env()
    record = await env.service.grant(env.request("online"))
    revoked = await env.service.revoke(UUID(int=9), record.id, "ошибка модерации")
    assert revoked.revoked_by == UUID(int=9)
    assert revoked.revoke_reason == "ошибка модерации"
    assert await env.service.active_mask(PLAYER) == 0
    assert env.last_event().mask == 0
    assert env.last_event().changed_by == UUID(int=9)
    count = len(env.world.events)
    again = await env.service.revoke(UUID(int=9), record.id, "ещё раз")
    assert again.revoke_reason == "ошибка модерации"  # повтор - та же запись
    assert len(env.world.events) == count
    assert env.world.versions[PLAYER] == 2  # впустую версия не растёт


async def test_revoke_unknown_and_self() -> None:
    env = Env()
    with pytest.raises(NotFoundError) as err:
        await env.service.revoke(MODERATOR, uuid7(), "x")
    assert err.value.code == "social.sanction_not_found"
    mine = await env.service.grant(env.request(user=PLAYER))
    with pytest.raises(ForbiddenError) as forbidden:
        await env.service.revoke(PLAYER, mine.id, "сам себе")
    assert forbidden.value.code == "social.self_sanction_forbidden"


async def test_expiry_closes_due_and_publishes_with_null_actor() -> None:
    env = Env()
    await env.service.grant(env.request("online", 60))
    await env.service.grant(env.request("ranked", None))
    env.clock.advance(timedelta(minutes=2))
    assert await env.service.expire_due() == 1
    event = env.last_event()
    assert event.changed_by is None
    assert event.mask == int(Sanction.RANKED)
    assert event.version == 3
    assert await env.service.expire_due() == 0  # идемпотентно
    assert len(env.world.events) == 3


async def test_idempotent_grant_replays_same_record() -> None:
    env = Env()
    request = env.request("online")
    first, executed = await env.service.grant_idempotent(request, "key-1")
    again, executed_again = await env.service.grant_idempotent(request, "key-1")
    assert executed and not executed_again
    assert again == first
    assert len(env.world.events) == 1


async def test_idempotency_key_reused_with_other_body_conflicts() -> None:
    from gv_core.errors import ConflictError

    env = Env()
    await env.service.grant_idempotent(env.request("online"), "key-1")
    with pytest.raises(ConflictError):
        await env.service.grant_idempotent(env.request("ranked"), "key-1")


async def test_unknown_player_is_404_for_admin_api_only() -> None:
    env = Env()
    stranger = UUID(int=77)
    with pytest.raises(NotFoundError) as err:
        await env.service.grant_idempotent(env.request(user=stranger), "k")
    assert err.value.code == "social.player_not_found"
    await env.service.grant(env.request(user=stranger))  # порт для F4 игрока не проверяет


async def test_revoking_account_ban_needs_account_permission() -> None:
    env = Env()
    ban = await env.service.grant(env.request("account", None, perms=ACCOUNT))
    other = UUID(int=9)
    with pytest.raises(ForbiddenError) as err:
        await env.service.revoke(other, ban.id, "ошибка бана", MANAGE)
    assert err.value.code == "social.account_sanction_forbidden"
    assert env.world.records[ban.id].revoked_at is None
    assert len(env.world.events) == 1  # снятия не было
    revoked = await env.service.revoke(other, ban.id, "ошибка бана", ACCOUNT)
    assert revoked.revoked_by == other
    assert env.last_event().account_banned is False


async def test_revoking_other_kinds_does_not_need_account_permission() -> None:
    env = Env()
    record = await env.service.grant(env.request("online"))
    revoked = await env.service.revoke(UUID(int=9), record.id, "снято", MANAGE)
    assert revoked.revoked_at is not None


@pytest.mark.parametrize("reason", ["system:replaced", "system:expired", "system:anything"])
async def test_moderator_reason_cannot_use_reserved_system_prefix(reason: str) -> None:
    env = Env()
    record = await env.service.grant(env.request("online"))
    with pytest.raises(InvalidInputError) as err:
        await env.service.revoke(UUID(int=9), record.id, reason, MANAGE)
    assert err.value.code == "social.reason_reserved"
    assert env.world.records[record.id].revoked_at is None
