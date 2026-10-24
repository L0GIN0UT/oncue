"""Санкции в identity: маска в токене, бан аккаунта, версии событий, отзыв сессий (ADR 0052)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from identity_service.application.commands.user_sanctions import ApplyUserSanctions
from identity_service.domain import SessionKind

from gv_auth import Sanction
from gv_core import ForbiddenError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

PASSWORD = "correct horse battery"
CHAT = int(Sanction.CHAT_MUTE | Sanction.COMMENTS)


def _event(
    world: Any,
    user_id: UUID,
    version: int,
    active: list[tuple[str, int | None]],
) -> dict[str, Any]:
    """Payload ``UserSanctionsChanged``: ``active`` - (вид, срок в секундах от «сейчас»)."""
    now = world.clock.now()
    entries = [
        {
            "kind": kind,
            "expires_at": (now + timedelta(seconds=ttl)).isoformat() if ttl is not None else None,
            "sanction_id": str(uuid7()),
        }
        for kind, ttl in active
    ]
    from gv_auth import mask_of_kinds

    expiries = [e["expires_at"] for e in entries if e["expires_at"]]
    return {
        "account_banned": any(k == "account" for k, _ in active),
        "active": entries,
        "at": now.isoformat(),
        "changed_by": str(uuid7()),
        "mask": mask_of_kinds(k for k, _ in active),
        "next_expiry_at": min(expiries) if expiries else None,
        "user_id": str(user_id),
        "version": version,
    }


async def _deliver(world: Any, payload: dict[str, Any]) -> Any:
    async with world.uow() as uow:
        result = await ApplyUserSanctions(uow.user_sanctions, uow.sessions, world.clock)(payload)
        await uow.commit()
    world.revoked.update(result.revoked_sessions)
    return result


async def test_token_mask_is_zero_without_sanctions(world: Any) -> None:
    await world.make_user()
    await world.login("neo_2026", PASSWORD)
    assert world.tokens.sanctions_issued[-1] == 0


async def test_token_carries_mask_from_event(world: Any) -> None:
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("chat", None), ("online", 3600)]))
    await world.login("neo_2026", PASSWORD)
    assert world.tokens.sanctions_issued[-1] == CHAT | int(Sanction.ONLINE)


async def test_expired_sanction_is_not_in_token(world: Any) -> None:
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("online", 600), ("ranked", None)]))
    world.advance(601)
    await world.login("neo_2026", PASSWORD)
    assert world.tokens.sanctions_issued[-1] == int(Sanction.RANKED)


async def test_refresh_reissues_token_with_current_mask(world: Any) -> None:
    user_id = await world.make_user()
    issued = await world.login("neo_2026", PASSWORD)
    await _deliver(world, _event(world, user_id, 1, [("ranked", None)]))
    await world.uc.refresh(issued.refresh_token, kind=SessionKind.WEB)
    assert world.tokens.sanctions_issued[-1] == int(Sanction.RANKED)


async def test_lifting_restores_clean_token(world: Any) -> None:
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("online", None)]))
    await _deliver(world, _event(world, user_id, 2, []))
    await world.login("neo_2026", PASSWORD)
    assert world.tokens.sanctions_issued[-1] == 0


async def test_stale_and_duplicate_events_are_dropped(world: Any) -> None:
    user_id = await world.make_user()
    assert (await _deliver(world, _event(world, user_id, 5, [("online", None)]))).applied
    older = await _deliver(world, _event(world, user_id, 4, []))
    duplicate = await _deliver(world, _event(world, user_id, 5, []))
    assert not older.applied
    assert not duplicate.applied
    await world.login("neo_2026", PASSWORD)
    assert world.tokens.sanctions_issued[-1] == int(Sanction.ONLINE)


async def test_banned_account_cannot_login_with_neutral_error(world: Any) -> None:
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    with pytest.raises(ForbiddenError) as info:
        await world.login("neo_2026", PASSWORD)
    assert info.value.code == "auth.account_banned"
    assert "бан" not in info.value.message.lower()
    assert info.value.details == {}


async def test_banned_account_on_new_device_gets_no_code_mail(world: Any) -> None:
    """Бан проверяется сразу после пароля: ни письма с кодом, ни challenge для забаненного."""
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    mails, challenges = len(world.emails), len(world.data.challenges)
    with pytest.raises(ForbiddenError) as info:
        await world.uc.login("neo_2026", PASSWORD, ip=None, device_token=None)
    assert info.value.code == "auth.account_banned"
    assert len(world.emails) == mails
    assert len(world.data.challenges) == challenges


async def test_wrong_password_on_new_device_for_banned_account_looks_ordinary(world: Any) -> None:
    from gv_core import UnauthenticatedError

    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    with pytest.raises(UnauthenticatedError) as info:
        await world.uc.login("neo_2026", "wrong password here", ip=None, device_token=None)
    assert info.value.code != "auth.account_banned"


async def test_banned_account_cannot_admin_login(world: Any) -> None:
    user_id = await world.make_user(roles=frozenset({"user", "moderator"}))
    await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    with pytest.raises(ForbiddenError) as info:
        await world.uc.admin_login("neo_2026", PASSWORD, ip=None)
    assert info.value.code == "auth.account_banned"


async def test_wrong_password_does_not_reveal_ban(world: Any) -> None:
    from gv_core import UnauthenticatedError

    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    with pytest.raises(UnauthenticatedError):
        await world.uc.login("neo_2026", "wrong password here", ip=None)


async def test_ban_event_revokes_sessions_and_blocks_refresh(world: Any) -> None:
    user_id = await world.make_user()
    first = await world.login("neo_2026", PASSWORD)
    second = await world.login("neo_2026", PASSWORD)
    result = await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    assert {first.session_id, second.session_id} <= set(result.revoked_sessions)
    assert {first.session_id, second.session_id} <= world.revoked  # denylist sid
    assert world.data.sessions[first.session_id].revoke_reason == "account_banned"
    from gv_core import UnauthenticatedError

    with pytest.raises((UnauthenticatedError, ForbiddenError)):
        await world.uc.refresh(first.refresh_token, kind=SessionKind.WEB)


async def test_refresh_of_banned_account_is_403_even_if_session_survived(world: Any) -> None:
    """Событие применилось, а сессию не отозвали (например, гонка): refresh всё равно закрыт."""
    user_id = await world.make_user()
    issued = await world.login("neo_2026", PASSWORD)
    async with world.uow() as uow:
        from identity_service.domain.user_sanctions import UserSanctions

        from gv_auth import SanctionEntry

        await uow.user_sanctions.apply(
            UserSanctions(
                user_id=user_id,
                version=1,
                entries=(SanctionEntry("account", None),),
                mask=0,
                account_banned=True,
                next_expiry_at=None,
                updated_at=world.clock.now(),
            )
        )
        await uow.commit()
    with pytest.raises(ForbiddenError) as info:
        await world.uc.refresh(issued.refresh_token, kind=SessionKind.WEB)
    assert info.value.code == "auth.account_banned"


async def test_new_token_after_ban_lifted(world: Any) -> None:
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("account", None)]))
    with pytest.raises(ForbiddenError):
        await world.login("neo_2026", PASSWORD)
    await _deliver(world, _event(world, user_id, 2, []))
    session = await world.login("neo_2026", PASSWORD)
    assert session.access_token
    assert world.tokens.sanctions_issued[-1] == 0


async def test_expired_account_ban_does_not_block_login(world: Any) -> None:
    user_id = await world.make_user()
    await _deliver(world, _event(world, user_id, 1, [("account", 600)]))
    world.advance(601)
    session = await world.login("neo_2026", PASSWORD)
    assert session.access_token


async def test_expired_ban_event_does_not_revoke_sessions(world: Any) -> None:
    user_id = await world.make_user()
    issued = await world.login("neo_2026", PASSWORD)
    payload = _event(world, user_id, 1, [("account", 60)])
    world.advance(61)
    result = await _deliver(world, payload)
    assert result.revoked_sessions == ()
    assert world.data.sessions[issued.session_id].is_active


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("version"),
        lambda p: p.update(version=0),
        lambda p: p.update(version="1"),
        lambda p: p.update(mask=-1),
        lambda p: p.update(mask=70000),
        lambda p: p.update(account_banned="yes"),
        lambda p: p.update(user_id="не-uuid"),
        lambda p: p.update(active=[{"kind": "online"}]),
        lambda p: p.update(next_expiry_at="2026-10-31T12:00:00"),
    ],
)
async def test_broken_payload_raises_contract_error(world: Any, mutate: Any) -> None:
    payload = _event(world, uuid7(), 1, [("online", 600)])
    mutate(payload)
    with pytest.raises((KeyError, ValueError, TypeError)):
        await _deliver(world, payload)
