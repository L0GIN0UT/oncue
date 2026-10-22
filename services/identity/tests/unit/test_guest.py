"""Гостевые сессии (D5 плана этапа 2): домен, выдача, ротация, завершение, очистка."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from identity_service.application.commands.auth import RefreshSession
from identity_service.application.commands.guest import (
    EndGuestSession,
    PurgeGuestSessions,
    StartGuestSession,
)
from identity_service.application.commands.guest_issuer import GuestConfig, GuestSessionIssuer
from identity_service.application.ports import IssuedAccess
from identity_service.application.security import hash_secret
from identity_service.application.sessions import SessionIssuer
from identity_service.domain import Session, SessionKind
from identity_service.domain.guest import (
    GUEST_UPGRADED_REASON,
    SubjectKind,
    is_abandoned,
    open_guest_session,
    subject_kind,
)
from identity_service.infrastructure.security import JwtIssuer, SigningKey, StaticKeyProvider

from gv_core.errors import ConflictError, RateLimitedError, UnauthenticatedError
from gv_core.ids import uuid7
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
CONFIG = GuestConfig(per_ip_per_hour=2, refresh_ttl_s=7200, revoked_retention_s=600)


# ----------------------------------------------------------------------------- домен
def test_session_has_exactly_one_subject() -> None:
    with pytest.raises(ValueError, match="ровно один"):
        Session(id=uuid7(), user_id=None, kind=SessionKind.WEB, created_at=NOW)
    with pytest.raises(ValueError, match="ровно один"):
        Session(id=uuid7(), user_id=uuid7(), guest_id=uuid7(), kind=SessionKind.WEB, created_at=NOW)
    with pytest.raises(ValueError, match="web"):
        Session(id=uuid7(), user_id=None, guest_id=uuid7(), kind=SessionKind.ADMIN, created_at=NOW)


def test_open_guest_session() -> None:
    sid, gid = uuid7(), uuid7()
    session = open_guest_session(session_id=sid, guest_id=gid, now=NOW, ip_prefix="10.0.0.0/24")
    assert (session.id, session.guest_id, session.user_id) == (sid, gid, None)
    assert session.is_guest and session.kind is SessionKind.WEB
    assert session.last_seen_at == NOW
    assert subject_kind(session) is SubjectKind.GUEST


def test_is_abandoned() -> None:
    guest = open_guest_session(session_id=uuid7(), guest_id=uuid7(), now=NOW, ip_prefix=None)
    user = Session(id=uuid7(), user_id=uuid7(), kind=SessionKind.WEB, created_at=NOW)
    rule = {"idle_s": 3600, "revoked_retention_s": 600}
    assert not is_abandoned(guest, now=NOW + timedelta(seconds=3599), **rule)
    assert is_abandoned(guest, now=NOW + timedelta(seconds=3600), **rule)
    assert not is_abandoned(user, now=NOW + timedelta(days=365), **rule)
    guest.revoke(NOW, "logout")
    assert not is_abandoned(guest, now=NOW + timedelta(seconds=599), **rule)
    assert is_abandoned(guest, now=NOW + timedelta(seconds=600), **rule)


# ----------------------------------------------------------------------------- токен
def test_jwt_guest_access_has_no_rights() -> None:
    private = ed25519.Ed25519PrivateKey.generate()
    keys = StaticKeyProvider([SigningKey("k1", private, active=True)], FakeClock(NOW))
    issuer = JwtIssuer(keys, FakeClock(NOW), issuer="iss", audience="guessverse", access_ttl_s=900)
    gid, sid = uuid7(), uuid7()

    issued = issuer.issue_guest_access(guest_id=gid, session_id=sid)

    claims = jwt.decode(issued.token, options={"verify_signature": False})
    assert claims["sub"] == str(gid) and claims["sid"] == str(sid)
    assert claims["roles"] == ["guest"] and claims["perms"] == []
    assert claims["gst"] is True
    assert "plan" not in claims
    assert claims["exp"] - claims["iat"] == 900 == issued.expires_in


# ----------------------------------------------------------------------------- use-cases
class FakeUserTokens:
    def issue_access(self, *, user_id: UUID, session_id: UUID, **_: object) -> IssuedAccess:
        return IssuedAccess(f"access:{user_id}:{session_id}", 900)


class Revocation:
    def __init__(self, world: Any) -> None:
        self.world = world

    async def revoke(self, session_ids: Any) -> None:
        self.world.revoked.update(session_ids)


def user_issuer(world: Any) -> SessionIssuer:
    return SessionIssuer(
        FakeUserTokens(), world.secrets, world.clock, refresh_ttl_s=world.config.refresh_ttl_s
    )


class FakeGuestTokens:
    def issue_guest_access(self, *, guest_id: UUID, session_id: UUID) -> IssuedAccess:
        return IssuedAccess(f"guest:{guest_id}:{session_id}", 900)


def guest_issuer(world: Any) -> GuestSessionIssuer:
    return GuestSessionIssuer(
        FakeGuestTokens(), user_issuer(world), world.clock, refresh_ttl_s=CONFIG.refresh_ttl_s
    )


def start(world: Any, issuer: GuestSessionIssuer | None = None) -> StartGuestSession:
    return StartGuestSession(
        world.uow, issuer or guest_issuer(world), world.limiter, world.clock, CONFIG
    )


def refresh(world: Any, issuer: GuestSessionIssuer) -> RefreshSession:
    return RefreshSession(
        world.uow, user_issuer(world), Revocation(world), world.clock, guests=issuer
    )


async def test_start_issues_guest_session_with_guest_ttl(world: Any) -> None:
    started: list[int] = []
    use_case = StartGuestSession(
        world.uow,
        guest_issuer(world),
        world.limiter,
        world.clock,
        CONFIG,
        on_started=lambda: started.append(1),
    )

    issued = await use_case(ip="10.1.2.3", refresh_token=None, authenticated=False)

    session = world.data.sessions[issued.session_id]
    assert session.is_guest and session.guest_id == issued.user_id
    assert session.ip_prefix == "10.1.2.0/24"  # точный IP не хранится
    assert issued.is_guest and issued.perms == frozenset() and issued.roles == {"guest"}
    assert issued.refresh_ttl_s == CONFIG.refresh_ttl_s
    token = next(iter(world.data.refresh.values()))
    assert token.expires_at - token.created_at == timedelta(seconds=CONFIG.refresh_ttl_s)
    assert started == [1]


async def test_start_refuses_over_live_session(world: Any) -> None:
    use_case = start(world)
    with pytest.raises(ConflictError, match="сессия"):
        await use_case(ip="10.1.2.3", refresh_token=None, authenticated=True)
    first = await use_case(ip="10.1.2.3", refresh_token=None, authenticated=False)
    with pytest.raises(ConflictError) as caught:
        await use_case(ip="10.1.2.3", refresh_token=first.refresh_token, authenticated=False)
    assert caught.value.code == "auth.already_authenticated"
    assert len(world.data.sessions) == 1


async def test_start_allows_new_session_over_dead_refresh(world: Any) -> None:
    use_case = start(world)
    first = await use_case(ip="10.1.2.3", refresh_token=None, authenticated=False)
    world.clock.advance(seconds=CONFIG.refresh_ttl_s)
    second = await use_case(ip="10.1.2.3", refresh_token=first.refresh_token, authenticated=False)
    assert second.session_id != first.session_id
    unknown = await use_case(ip="10.1.2.4", refresh_token="garbage", authenticated=False)
    assert unknown.session_id not in (first.session_id, second.session_id)


async def test_start_rate_limited_per_ip_including_missing_ip(world: Any) -> None:
    use_case = start(world)
    for ip in ("10.0.0.1", "10.0.0.1", None, None):
        await use_case(ip=ip, refresh_token=None, authenticated=False)
    for ip in ("10.0.0.1", None):
        with pytest.raises(RateLimitedError):
            await use_case(ip=ip, refresh_token=None, authenticated=False)
    await use_case(ip="10.0.0.2", refresh_token=None, authenticated=False)


async def test_refresh_rotates_guest_and_reuse_revokes(world: Any) -> None:
    issuer = guest_issuer(world)
    first = await start(world, issuer)(ip=None, refresh_token=None, authenticated=False)
    use_case = refresh(world, issuer)

    rotated = await use_case(first.refresh_token, kind=SessionKind.WEB)

    assert rotated.is_guest and rotated.session_id == first.session_id
    assert rotated.access_token.startswith("guest:")
    new = next(
        t for t in world.data.refresh.values() if t.token_hash == hash_secret(rotated.refresh_token)
    )
    assert new.expires_at - new.created_at == timedelta(seconds=CONFIG.refresh_ttl_s)

    with pytest.raises(UnauthenticatedError) as caught:
        await use_case(first.refresh_token, kind=SessionKind.WEB)
    assert caught.value.code == "auth.refresh_reused"
    assert first.session_id in world.revoked
    assert not world.data.sessions[first.session_id].is_active


async def test_guest_refresh_never_becomes_admin_or_works_without_guest_issuer(world: Any) -> None:
    issuer = guest_issuer(world)
    first = await start(world, issuer)(ip=None, refresh_token=None, authenticated=False)
    for call in (
        refresh(world, issuer)(first.refresh_token, kind=SessionKind.ADMIN),
        refresh(world, issuer)(
            first.refresh_token, kind=SessionKind.WEB, required_perm="admin.access"
        ),
        world.uc.refresh(first.refresh_token, kind=SessionKind.WEB),  # без guests
    ):
        with pytest.raises(UnauthenticatedError) as caught:
            await call
        assert caught.value.code == "auth.refresh_invalid"


async def test_end_revokes_only_guest_sessions(world: Any) -> None:
    guest = await start(world)(ip=None, refresh_token=None, authenticated=False)
    await world.make_user()
    user = await world.login("neo_2026", "correct horse battery", ip=None)
    end = EndGuestSession(world.uow, Revocation(world), world.clock)

    assert await end(refresh_token=user.refresh_token) is False
    assert await end(session_id=user.session_id) is False
    assert world.data.sessions[user.session_id].is_active

    assert await end(refresh_token=guest.refresh_token) is True
    assert world.data.sessions[guest.session_id].revoke_reason == GUEST_UPGRADED_REASON
    assert guest.session_id in world.revoked
    assert await end(refresh_token=guest.refresh_token) is False  # идемпотентно
    assert await end() is False


class FakePurger:
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = sizes
        self.calls: list[dict[str, Any]] = []

    async def purge(self, *, idle_before: datetime, revoked_before: datetime, limit: int) -> int:
        self.calls.append({"idle": idle_before, "revoked": revoked_before, "limit": limit})
        return self.sizes.pop(0) if self.sizes else 0


async def test_purge_batches_until_short_batch_and_caps_batches() -> None:
    clock = FakeClock(NOW)
    config = GuestConfig(refresh_ttl_s=7200, revoked_retention_s=600, purge_batch=10)
    purger = FakePurger([10, 10, 3, 10])
    assert await PurgeGuestSessions(purger, clock, config)() == 23
    assert purger.calls[0] == {
        "idle": NOW - timedelta(seconds=7200),
        "revoked": NOW - timedelta(seconds=600),
        "limit": 10,
    }
    capped = FakePurger([10] * 50)
    limited = GuestConfig(purge_batch=10, purge_max_batches=3)
    assert await PurgeGuestSessions(capped, clock, limited)() == 30
    assert len(capped.calls) == 3
