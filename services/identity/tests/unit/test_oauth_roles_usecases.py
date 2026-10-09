"""Use-cases привязки Яндекса, ролей, согласий и удаления аккаунта."""

from __future__ import annotations

from typing import Any

import pytest
from identity_service.application.dto import Actor, RegisterCommand
from identity_service.application.security import OAuthStateError, pkce_challenge
from identity_service.domain import (
    ConsentRequirement,
    ExternalAccountLinked,
    ExternalAccountUnlinked,
    Provider,
    UserDeleted,
    UserRolesChanged,
)

from gv_core import (
    ConflictError,
    FeatureDisabledError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

PASSWORD = "correct horse battery"
Y = Provider.YANDEX


async def _actor(world: Any, nickname: str = "neo_2026", email: str = "neo@example.com") -> Actor:
    user_id = await world.make_user(nickname, email)
    issued = await world.uc.login(nickname, PASSWORD, ip=None)
    return Actor(user_id, issued.roles, issued.perms, issued.session_id)


def _state(url: str) -> str:
    return url.split("state=", 1)[1].split("&", 1)[0]


async def _link(world: Any, actor: Actor, code: str, subject: str) -> Any:
    started = await world.uc.start_oauth(
        Y, purpose="link", actor=actor, return_to="/profile", password=PASSWORD
    )
    world.yandex.will_return(code, subject)
    return await world.uc.complete_oauth(
        Y,
        state=_state(started.authorize_url),
        code=code,
        browser_nonce=started.browser_nonce,
        actor=actor,
        ip=None,
    )


async def test_link_flow_uses_pkce_and_publishes_event(world: Any) -> None:
    actor = await _actor(world)
    world.events.clear()
    result = await _link(world, actor, "c1", "ya-1")
    assert result.purpose == "link"
    assert result.return_to == "/profile"
    # PKCE: провайдер получил verifier, соответствующий отправленному challenge.
    assert pkce_challenge(world.yandex.verifiers["c1"]) == world.yandex.last_challenge
    (event,) = world.events
    assert isinstance(event, ExternalAccountLinked)
    assert "ya-1" not in str(event.payload())  # внешний subject не публикуется
    linked = await world.uc.list_linked(actor.user_id)
    assert [(a.provider, a.display) for a in linked] == [("yandex", "ya_user")]


async def test_link_requires_login(world: Any) -> None:
    with pytest.raises(UnauthenticatedError):
        await world.uc.start_oauth(Y, purpose="link", actor=None, return_to=None)


async def test_state_is_single_use_and_bound_to_browser(world: Any) -> None:
    actor = await _actor(world)
    started = await world.uc.start_oauth(
        Y, purpose="link", actor=actor, return_to=None, password=PASSWORD
    )
    state = _state(started.authorize_url)
    world.yandex.will_return("c1", "ya-1")
    with pytest.raises(OAuthStateError):  # другой браузер
        await world.uc.complete_oauth(
            Y, state=state, code="c1", browser_nonce="other", actor=actor, ip=None
        )
    with pytest.raises(OAuthStateError):  # state уже забран
        await world.uc.complete_oauth(
            Y, state=state, code="c1", browser_nonce=started.browser_nonce, actor=actor, ip=None
        )
    with pytest.raises(OAuthStateError):
        await world.uc.complete_oauth(
            Y, state="forged", code="c1", browser_nonce="x", actor=actor, ip=None
        )


async def test_link_rejected_when_starting_session_revoked(world: Any) -> None:
    actor = await _actor(world)
    started = await world.uc.start_oauth(
        Y, purpose="link", actor=actor, return_to=None, password=PASSWORD
    )
    await world.uc.logout(session_id=actor.session_id)
    world.yandex.will_return("c1", "ya-1")
    with pytest.raises(UnauthenticatedError) as info:
        await world.uc.complete_oauth(
            Y,
            state=_state(started.authorize_url),
            code="c1",
            browser_nonce=started.browser_nonce,
            actor=None,
            ip=None,
        )
    assert info.value.code == "auth.session_revoked"


async def test_link_by_other_user_than_state_owner_rejected(world: Any) -> None:
    neo = await _actor(world)
    trinity = await _actor(world, "trinity", "trinity@example.com")
    started = await world.uc.start_oauth(
        Y, purpose="link", actor=neo, return_to=None, password=PASSWORD
    )
    world.yandex.will_return("c1", "ya-1")
    with pytest.raises(OAuthStateError):
        await world.uc.complete_oauth(
            Y,
            state=_state(started.authorize_url),
            code="c1",
            browser_nonce=started.browser_nonce,
            actor=trinity,
            ip=None,
        )


async def test_provider_account_linked_to_other_user_is_conflict(world: Any) -> None:
    neo = await _actor(world)
    trinity = await _actor(world, "trinity", "trinity@example.com")
    await _link(world, neo, "c1", "ya-1")
    with pytest.raises(ConflictError) as info:
        await _link(world, trinity, "c2", "ya-1")
    assert info.value.code == "auth.provider_account_taken"


async def test_second_yandex_for_same_user_is_conflict(world: Any) -> None:
    neo = await _actor(world)
    await _link(world, neo, "c1", "ya-1")
    with pytest.raises(ConflictError) as info:
        await world.uc.start_oauth(Y, purpose="link", actor=neo, return_to=None, password=PASSWORD)
    assert info.value.code == "auth.provider_already_linked"


async def test_login_via_unlinked_yandex_is_conflict_and_creates_nothing(world: Any) -> None:
    await world.make_user()
    users_before = dict(world.data.users)
    started = await world.uc.start_oauth(Y, purpose="login", actor=None, return_to=None)
    # У провайдера тот же e-mail — но автослияния нет: только привязанные аккаунты.
    world.yandex.will_return("c9", "ya-unknown", display="neo@example.com")
    with pytest.raises(ConflictError) as info:
        await world.uc.complete_oauth(
            Y,
            state=_state(started.authorize_url),
            code="c9",
            browser_nonce=started.browser_nonce,
            actor=None,
            ip=None,
        )
    assert info.value.code == "auth.provider_not_linked"
    assert world.data.users.keys() == users_before.keys()


async def test_login_via_linked_yandex(world: Any) -> None:
    neo = await _actor(world)
    await _link(world, neo, "c1", "ya-1")
    started = await world.uc.start_oauth(Y, purpose="login", actor=None, return_to="/play")
    world.yandex.will_return("c2", "ya-1")
    result = await world.uc.complete_oauth(
        Y,
        state=_state(started.authorize_url),
        code="c2",
        browser_nonce=started.browser_nonce,
        actor=None,
        ip="10.1.1.1",
    )
    assert result.session is not None
    assert result.session.user_id == neo.user_id
    assert result.return_to == "/play"


@pytest.mark.parametrize(
    "return_to",
    ["https://evil.test", "//evil.test", "/\\evil.test", "javascript:alert(1)", "/a\nb"],
)
async def test_open_redirect_rejected(world: Any, return_to: str) -> None:
    with pytest.raises(OAuthStateError) as info:
        await world.uc.start_oauth(Y, purpose="login", actor=None, return_to=return_to)
    assert info.value.code == "auth.return_to_invalid"


async def test_disabled_provider(world: Any) -> None:
    with pytest.raises(FeatureDisabledError):
        await world.uc.start_oauth(Provider.SPOTIFY, purpose="login", actor=None, return_to=None)


async def test_unlink(world: Any) -> None:
    neo = await _actor(world)
    await _link(world, neo, "c1", "ya-1")
    world.events.clear()
    await world.uc.unlink(neo.user_id, Y, password=PASSWORD, ip=None)
    assert isinstance(world.events[0], ExternalAccountUnlinked)
    with pytest.raises(NotFoundError):
        await world.uc.unlink(neo.user_id, Y, password=PASSWORD, ip=None)


# ------------------------------------------------------------------------ роли


async def _admin(world: Any) -> Actor:
    user_id = await world.make_user("boss", "boss@example.com")  # bootstrap admin
    matrix = world.data.matrix
    roles = world.data.users[user_id].roles
    return Actor(user_id, roles, matrix.effective_perms(roles))


async def test_assign_roles_event_and_audit(world: Any) -> None:
    admin = await _admin(world)
    target = await world.make_user()
    world.events.clear()
    result = await world.uc.assign_roles(
        admin, target, frozenset({"moderator"}), "помощник", "key-0001"
    )
    assert "catalog.block_item" in result["perms"]
    (event,) = world.events
    assert isinstance(event, UserRolesChanged)
    assert event.roles == ("moderator", "user")  # роль user сохраняется всегда
    assert event.actor_id == admin.user_id
    assert world.data.audit[-1].reason == "помощник"
    with pytest.raises(InvalidInputError):
        await world.uc.assign_roles(admin, target, frozenset({"moderator"}), "  ", "key-0002")


async def test_moderator_cannot_escalate_or_demote_admin(world: Any) -> None:
    admin = await _admin(world)
    mod_id = await world.make_user("mod_1", "mod@example.com")
    await world.uc.assign_roles(admin, mod_id, frozenset({"moderator"}), "x", "key-0001")
    roles = world.data.users[mod_id].roles
    moderator = Actor(mod_id, roles, world.data.matrix.effective_perms(roles))
    for n, (target, roles) in enumerate([(mod_id, {"admin"}), (admin.user_id, {"user"})]):
        with pytest.raises(ForbiddenError) as info:
            await world.uc.assign_roles(moderator, target, frozenset(roles), "x", f"key-esc-{n}")
        assert info.value.code == "roles.escalation"


async def test_matrix_update_publishes_snapshots_for_affected_users(world: Any) -> None:
    admin = await _admin(world)
    mod_id = await world.make_user("mod_1", "mod@example.com")
    await world.uc.assign_roles(admin, mod_id, frozenset({"moderator"}), "x", "key-0001")
    world.events.clear()
    roles = {name: set(perms) for name, perms in world.data.matrix.roles.items()}
    roles["moderator"].add("catalog.import")
    await world.uc.update_role_matrix(
        admin, {k: frozenset(v) for k, v in roles.items()}, "импорт модераторам", "key-0002"
    )
    (event,) = world.events
    assert isinstance(event, UserRolesChanged)
    assert event.user_id == mod_id
    assert "catalog.import" in event.perms


async def test_matrix_guards(world: Any) -> None:
    admin = await _admin(world)
    base = dict(world.data.matrix.roles)
    with pytest.raises(ForbiddenError) as info:  # админ лишает себя управления ролями
        await world.uc.update_role_matrix(
            admin, {**base, "admin": base["admin"] - {"roles.manage"}}, "x", "key-g1"
        )
    assert info.value.code == "roles.lockout"
    mod_id = await world.make_user("mod_1", "mod@example.com")
    await world.uc.assign_roles(admin, mod_id, frozenset({"moderator"}), "x", "key-0001")
    with pytest.raises(ConflictError) as info:  # удаление назначенной роли
        await world.uc.update_role_matrix(
            admin, {k: v for k, v in base.items() if k != "moderator"}, "x", "key-g2"
        )
    assert info.value.code == "roles.in_use"
    with pytest.raises(InvalidInputError):
        await world.uc.update_role_matrix(
            admin, {k: v for k, v in base.items() if k != "user"}, "x", "key-g3"
        )
    with pytest.raises(InvalidInputError):
        await world.uc.update_role_matrix(
            admin, {**base, "x": frozenset({"no.such"})}, "x", "key-g4"
        )


# ------------------------------------------------------------------------ аккаунт


async def test_delete_account(world: Any) -> None:
    neo = await _actor(world)
    await _link(world, neo, "c1", "ya-1")
    with pytest.raises(UnauthenticatedError):
        await world.uc.delete_account(neo.user_id, "wrong password!", ip=None)
    world.events.clear()
    await world.uc.delete_account(neo.user_id, PASSWORD, ip=None)
    assert isinstance(world.events[-1], UserDeleted)
    assert neo.session_id in world.revoked
    assert world.data.identities == {}
    with pytest.raises(UnauthenticatedError):
        await world.uc.login("neo_2026", PASSWORD, ip=None)
    # Ник и e-mail свободны.
    again = await world.uc.register(
        RegisterCommand(
            nickname="neo_2026",
            email="neo@example.com",
            password=PASSWORD,
            consents=world.config.required_consents,
        )
    )
    assert again is not None and again != neo.user_id


async def test_grant_consents_and_get_user(world: Any) -> None:
    user_id = await world.make_user()
    with pytest.raises(InvalidInputError):
        await world.uc.grant_consents(user_id, (ConsentRequirement("marketing", "1"),))
    await world.uc.grant_consents(user_id, (ConsentRequirement("terms", "2026-10"),))
    view = await world.uc.get_user(user_id)
    assert view.email == "neo@example.com"
    assert ConsentRequirement("terms", "2026-10") in view.consents
    page = await world.uc.list_users(None, cursor=None, limit=1)
    assert len(page.items) == 1
    assert page.next_cursor is None
