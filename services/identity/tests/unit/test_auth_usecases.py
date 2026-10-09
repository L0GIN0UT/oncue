"""Use-cases входа: регистрация, подтверждение, вход, refresh, выход, сброс пароля."""

from __future__ import annotations

from typing import Any

import pytest
from identity_service.application.dto import RegisterCommand
from identity_service.domain import (
    ConsentRequirement,
    SessionKind,
    UserRegistered,
    UserRolesChanged,
)

from gv_core import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    RateLimitedError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

TERMS = (ConsentRequirement("terms", "2026-10"), ConsentRequirement("privacy", "2026-10"))
PASSWORD = "correct horse battery"


def _cmd(**overrides: Any) -> RegisterCommand:
    data: dict[str, Any] = {
        "nickname": "neo_2026",
        "email": "Neo@Example.com",
        "password": PASSWORD,
        "consents": TERMS,
        "locale": "ru",
    }
    data.update(overrides)
    return RegisterCommand(**data)


# ------------------------------------------------------------------------ регистрация


async def test_register_creates_user_event_consents_and_verification_mail(world: Any) -> None:
    user_id = await world.uc.register(_cmd())
    user = world.data.users[user_id]
    assert user.email.value == "neo@example.com"
    assert user.roles == {"user"}
    assert world.data.creds[user_id] == f"h${PASSWORD}"
    assert {(c.kind, c.version) for c in world.data.consents} == {
        ("terms", "2026-10"),
        ("privacy", "2026-10"),
    }
    (event,) = world.events
    assert isinstance(event, UserRegistered)
    assert event.nickname == "neo_2026"
    (mail,) = world.emails
    assert mail.purpose == "verify"
    assert mail.link.startswith("https://gv.test/verify?token=")
    # В БД — только хэш токена из письма.
    assert world.last_link_token() not in str(world.data.email_tokens)


async def test_register_taken_email_looks_like_success(world: Any) -> None:
    await world.uc.register(_cmd())
    world.events.clear()
    result = await world.uc.register(_cmd(nickname="other_nick"))
    assert result is None
    assert world.events == []
    assert world.emails[-1].purpose == "already_registered"
    assert world.emails[-1].link is None


async def test_register_taken_nickname_is_conflict_case_insensitive(world: Any) -> None:
    await world.uc.register(_cmd())
    with pytest.raises(ConflictError) as info:
        await world.uc.register(_cmd(nickname="NEO_2026", email="x@example.com"))
    assert info.value.code == "user.nickname_taken"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"consents": TERMS[:1]}, "auth.consents_required"),
        ({"password": "qwertyuiop"}, "auth.password_weak"),
        ({"nickname": "x"}, "user.nickname_invalid"),
        ({"email": "bad"}, "user.email_invalid"),
        ({"locale": "русский"}, "user.locale_invalid"),
    ],
)
async def test_register_validation(world: Any, overrides: dict[str, Any], code: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        await world.uc.register(_cmd(**overrides))
    assert info.value.code == code
    assert world.data.users == {}


async def test_register_rate_limited_by_ip(world: Any) -> None:
    for i in range(10):
        await world.uc.register(_cmd(nickname=f"nick_{i}", email=f"u{i}@x.ru", ip="1.2.3.4"))
    with pytest.raises(RateLimitedError):
        await world.uc.register(_cmd(nickname="nick_x", email="ux@x.ru", ip="1.2.3.4"))


# ------------------------------------------------------------------------ подтверждение


async def test_verify_email_is_single_use(world: Any) -> None:
    user_id = await world.uc.register(_cmd())
    token = world.last_link_token()
    await world.uc.verify_email(token)
    assert world.data.users[user_id].email_verified
    with pytest.raises(InvalidInputError) as info:
        await world.uc.verify_email(token)
    assert info.value.code == "auth.email_token_invalid"


async def test_verify_expired_token(world: Any) -> None:
    await world.uc.register(_cmd())
    world.clock.advance(hours=25)
    with pytest.raises(InvalidInputError):
        await world.uc.verify_email(world.last_link_token())


async def test_bootstrap_admin_only_after_verification(world: Any) -> None:
    user_id = await world.uc.register(_cmd(email="boss@example.com"))
    assert world.data.users[user_id].roles == {"user"}
    await world.uc.verify_email(world.last_link_token())
    assert world.data.users[user_id].roles == {"user", "admin"}
    changed = [e for e in world.events if isinstance(e, UserRolesChanged)]
    assert changed[0].reason == "bootstrap_admin"
    assert "roles.manage" in changed[0].perms
    assert world.data.audit[0].kind == "user_roles"


# ------------------------------------------------------------------------ вход


@pytest.mark.parametrize("login", ["neo@example.com", "NEO@example.com", "neo_2026", "Neo_2026"])
async def test_login_by_email_or_nickname(world: Any, login: str) -> None:
    user_id = await world.make_user()
    issued = await world.uc.login(login, PASSWORD, ip="10.0.0.1")
    assert issued.user_id == user_id
    session = world.data.sessions[issued.session_id]
    assert session.kind is SessionKind.WEB
    assert session.ip_prefix == "10.0.0.0/24"
    assert issued.refresh_token not in str(world.data.refresh)  # хранится только хэш


async def test_unknown_user_and_wrong_password_are_indistinguishable(world: Any) -> None:
    await world.make_user()
    errors = []
    for login, password in [("ghost@example.com", PASSWORD), ("neo_2026", "wrong password!")]:
        with pytest.raises(UnauthenticatedError) as info:
            await world.uc.login(login, password, ip=None)
        errors.append((info.value.code, info.value.message))
    assert errors[0] == errors[1] == ("auth.invalid_credentials", errors[0][1])
    assert world.hasher.dummy_calls == 1  # для несуществующего — фиктивная проверка


async def test_invalid_login_format_is_just_invalid_credentials(world: Any) -> None:
    with pytest.raises(UnauthenticatedError):
        await world.uc.login("a b c", PASSWORD, ip=None)
    assert world.hasher.dummy_calls == 1


async def test_unverified_email_cannot_login(world: Any) -> None:
    await world.make_user(verified=False)
    with pytest.raises(ForbiddenError) as info:
        await world.uc.login("neo_2026", PASSWORD, ip=None)
    assert info.value.code == "auth.email_not_verified"


async def test_account_lockout_after_failures(world: Any) -> None:
    await world.make_user()
    for _ in range(5):
        with pytest.raises(UnauthenticatedError):
            await world.uc.login("neo_2026", "wrong password!", ip=None)
    with pytest.raises(RateLimitedError) as info:
        await world.uc.login("neo_2026", PASSWORD, ip=None)
    assert info.value.details["retry_after"] >= 1


async def test_ip_limit(world: Any) -> None:
    await world.make_user()
    for _ in range(10):
        await world.uc.login("neo_2026", PASSWORD, ip="9.9.9.9")
    with pytest.raises(RateLimitedError):
        await world.uc.login("neo_2026", PASSWORD, ip="9.9.9.9")


async def test_rehash_on_login(world: Any) -> None:
    user_id = await world.make_user()
    world.data.creds[user_id] = f"old${PASSWORD}"
    await world.uc.login("neo_2026", PASSWORD, ip=None)
    assert world.data.creds[user_id] == f"h${PASSWORD}"


async def test_admin_login_requires_admin_access(world: Any) -> None:
    await world.make_user()
    with pytest.raises(ForbiddenError) as info:
        await world.uc.admin_login("neo_2026", PASSWORD, ip=None)
    assert info.value.code == "auth.forbidden"
    await world.make_user("mod_1", "mod@example.com", roles=frozenset({"user", "moderator"}))
    issued = await world.uc.admin_login("mod_1", PASSWORD, ip=None)
    assert "admin.access" in issued.perms
    assert world.data.sessions[issued.session_id].kind is SessionKind.ADMIN


# ------------------------------------------------------------------------ refresh


async def test_refresh_rotates(world: Any) -> None:
    await world.make_user()
    first = await world.uc.login("neo_2026", PASSWORD, ip=None)
    world.clock.advance(minutes=20)
    second = await world.uc.refresh(first.refresh_token, kind=SessionKind.WEB)
    assert second.session_id == first.session_id
    assert second.refresh_token != first.refresh_token
    third = await world.uc.refresh(second.refresh_token, kind=SessionKind.WEB)
    assert third.session_id == first.session_id


async def test_refresh_reuse_revokes_session_and_denylists_sid(world: Any) -> None:
    await world.make_user()
    first = await world.uc.login("neo_2026", PASSWORD, ip=None)
    second = await world.uc.refresh(first.refresh_token, kind=SessionKind.WEB)
    with pytest.raises(UnauthenticatedError) as info:
        await world.uc.refresh(first.refresh_token, kind=SessionKind.WEB)
    assert info.value.code == "auth.refresh_reused"
    assert world.data.sessions[first.session_id].revoke_reason == "refresh_reuse"
    assert first.session_id in world.revoked
    # Легитимный (последний) токен семьи тоже больше не работает.
    with pytest.raises(UnauthenticatedError) as info:
        await world.uc.refresh(second.refresh_token, kind=SessionKind.WEB)
    assert info.value.code == "auth.session_revoked"


async def test_refresh_unknown_and_wrong_kind(world: Any) -> None:
    await world.make_user()
    issued = await world.uc.login("neo_2026", PASSWORD, ip=None)
    for token, kind in [("nope", SessionKind.WEB), (issued.refresh_token, SessionKind.ADMIN)]:
        with pytest.raises(UnauthenticatedError) as info:
            await world.uc.refresh(token, kind=kind)
        assert info.value.code == "auth.refresh_invalid"


async def test_admin_refresh_rechecks_permission(world: Any) -> None:
    user_id = await world.make_user(roles=frozenset({"user", "moderator"}))
    issued = await world.uc.admin_login("neo_2026", PASSWORD, ip=None)
    world.data.users[user_id].roles = frozenset({"user"})
    with pytest.raises(ForbiddenError):
        await world.uc.refresh(
            issued.refresh_token, kind=SessionKind.ADMIN, required_perm="admin.access"
        )


# ------------------------------------------------------------------------ выход


async def test_logout_and_logout_all(world: Any) -> None:
    user_id = await world.make_user()
    a = await world.uc.login("neo_2026", PASSWORD, ip=None)
    b = await world.uc.login("neo_2026", PASSWORD, ip=None)
    c = await world.uc.login("neo_2026", PASSWORD, ip=None)
    await world.uc.logout(session_id=a.session_id)
    await world.uc.logout(refresh_token=b.refresh_token)
    assert world.revoked == {a.session_id, b.session_id}
    await world.uc.logout(session_id=a.session_id)  # повтор — без ошибок
    assert await world.uc.logout_all(user_id) == 1
    assert c.session_id in world.revoked
    with pytest.raises(UnauthenticatedError):
        await world.uc.refresh(c.refresh_token, kind=SessionKind.WEB)


# ------------------------------------------------------------------------ сброс пароля


async def test_password_reset_flow(world: Any) -> None:
    user_id = await world.make_user()
    session = await world.uc.login("neo_2026", PASSWORD, ip=None)
    await world.uc.request_password_reset("neo@example.com", ip="1.1.1.1")
    token = world.last_link_token()
    assert world.emails[-1].purpose == "reset"
    with pytest.raises(InvalidInputError):
        await world.uc.reset_password(token, "short")
    await world.uc.reset_password(token, "brand new passphrase")
    assert world.data.creds[user_id] == "h$brand new passphrase"
    assert session.session_id in world.revoked
    with pytest.raises(InvalidInputError):
        await world.uc.reset_password(token, "another new passphrase")  # одноразовый


async def test_password_reset_for_unknown_email_is_silent(world: Any) -> None:
    await world.uc.request_password_reset("ghost@example.com", ip=None)
    await world.uc.request_password_reset("not-an-email", ip=None)
    assert world.emails == []
