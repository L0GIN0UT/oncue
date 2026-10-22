"""Регрессионные тесты security-ревью identity (H1, M1–M3, L3, L4, L6, идемпотентность admin).

Каждый тест воспроизводит найденный обход и падал до исправления.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from identity_service.application.dto import Actor, RegisterCommand
from identity_service.domain import SessionKind

from gv_core import (
    ConflictError,
    InvalidInputError,
    RateLimitedError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

PASSWORD = "correct horse battery"
WRONG = "wrong password!"


World = Any  # фейковый мир из conftest (импорт conftest в режиме importlib недоступен)
MakeWorld = Callable[..., Any]


async def _fail(world: World, login: str, *, ip: str | None = None) -> type[Exception]:
    """Неудачная попытка входа; возвращает класс ошибки."""
    try:
        await world.login(login, WRONG, ip=ip)
    except (UnauthenticatedError, RateLimitedError) as exc:
        return type(exc)
    raise AssertionError("вход с неверным паролем прошёл")


# ------------------------------------------------------------------------ H1a: Unicode-варианты


async def test_login_limit_shared_by_unicode_variants_and_email(world: World) -> None:
    """ａdmin/𝐚dmin/знак Кельвина не дают отдельных счётчиков: ключ — по аккаунту."""
    await world.make_user("Kevin_1", "kevin@example.com")
    variants = [
        "Kevin_1",
        "kevin_1",
        "Ｋevin_1",  # полноширинная K
        "Kevin_1",  # знак Кельвина → K после NFKC
        "\U0001d40aevin_1",  # математическая жирная K
    ]
    for login in variants:
        assert await _fail(world, login) is UnauthenticatedError
    # Шестая попытка (через e-mail того же аккаунта) — уже лимит, даже с верным паролем.
    with pytest.raises(RateLimitedError):
        await world.login("KEVIN@example.com", PASSWORD, ip=None)


# ------------------------------------------------------------------------ H1b: атомарность


async def test_parallel_attempts_cannot_exceed_account_limit(world: World) -> None:
    """peek→verify→hit пропускал параллельные запросы: теперь hit до проверки пароля."""
    await world.make_user()
    results = await asyncio.gather(
        *(world.login("neo_2026", WRONG, ip=None) for _ in range(20)),
        return_exceptions=True,
    )
    invalid = [r for r in results if isinstance(r, UnauthenticatedError)]
    limited = [r for r in results if isinstance(r, RateLimitedError)]
    assert len(invalid) == 5
    assert len(limited) == 15
    assert world.hasher.verify_calls == 5


async def test_successful_login_is_not_punished(world: World) -> None:
    await world.make_user()
    for _ in range(4):
        await _fail(world, "neo_2026")
    for _ in range(3):  # успешные входы не расходуют лимит неудач
        await world.login("neo_2026", PASSWORD, ip=None)
    assert await _fail(world, "neo_2026") is UnauthenticatedError


# ------------------------------------------------------------------------ L3: подсеть


async def test_account_limit_is_per_subnet_with_soft_global_limit(make_world: MakeWorld) -> None:
    world = make_world(login_per_account_subnet=3, login_per_account=7)
    await world.make_user()
    for _ in range(3):
        await _fail(world, "neo_2026", ip="198.51.100.7")
    # Та же /24 — заблокирована, владелец из другой сети входит.
    with pytest.raises(RateLimitedError):
        await world.login("neo_2026", PASSWORD, ip="198.51.100.200")
    await world.login("neo_2026", PASSWORD, ip="203.0.113.5")
    # IPv6: одна /64 — один счётчик.
    for suffix in ("1", "2", "3"):
        await _fail(world, "neo_2026", ip=f"2001:db8:0:1::{suffix}")
    with pytest.raises(RateLimitedError):
        await world.login("neo_2026", PASSWORD, ip="2001:db8:0:1::ffff")
    # Распределённый перебор из многих сетей упирается в общий мягкий лимит.
    for n in range(4):
        await _fail(world, "neo_2026", ip=f"192.0.{n}.1")
    with pytest.raises(RateLimitedError):
        await world.login("neo_2026", PASSWORD, ip="10.20.30.40")


# ------------------------------------------------------------------------ H1c: admin-сессии


async def test_admin_login_limited_by_client_ip(world: World) -> None:
    await world.make_user(roles=frozenset({"user", "moderator"}))
    for n in range(10):
        with pytest.raises(UnauthenticatedError):
            await world.uc.admin_login(f"ghost_{n}", WRONG, ip="203.0.113.9")
    with pytest.raises(RateLimitedError):
        await world.uc.admin_login("neo_2026", PASSWORD, ip="203.0.113.9")
    issued = await world.uc.admin_login("neo_2026", PASSWORD, ip="198.51.100.1")
    assert issued.identity_version == world.data.users[issued.user_id].version


# ------------------------------------------------------------------------ M1: понижение прав


async def _admin(world: World) -> Actor:
    user_id = await world.make_user("boss", "boss@example.com")
    roles = world.data.users[user_id].roles
    return Actor(user_id, roles, world.data.matrix.effective_perms(roles))


async def test_demotion_revokes_sessions(world: World) -> None:
    admin = await _admin(world)
    mod_id = await world.make_user("mod_1", "mod@example.com")
    await world.uc.assign_roles(admin, mod_id, frozenset({"moderator"}), "помощь", "k-up-0001")
    web = await world.login("mod_1", PASSWORD, ip=None)
    adm = await world.uc.admin_login("mod_1", PASSWORD, ip=None)
    assert not world.revoked  # повышение ничего не отзывает

    await world.uc.assign_roles(admin, mod_id, frozenset(), "больше не модератор", "k-down-001")
    assert {web.session_id, adm.session_id} <= world.revoked
    with pytest.raises(UnauthenticatedError):
        await world.uc.refresh(adm.refresh_token, kind=SessionKind.ADMIN)


async def test_matrix_perm_removal_revokes_affected_sessions(world: World) -> None:
    admin = await _admin(world)
    mod_id = await world.make_user("mod_1", "mod@example.com")
    await world.uc.assign_roles(admin, mod_id, frozenset({"moderator"}), "помощь", "k-up-0001")
    mod_session = await world.login("mod_1", PASSWORD, ip=None)
    roles = dict(world.data.matrix.roles)
    roles["moderator"] = roles["moderator"] | {"catalog.import"}
    await world.uc.update_role_matrix(admin, roles, "расширение", "k-matrix-01")
    assert mod_session.session_id not in world.revoked
    roles["moderator"] = roles["moderator"] - {"catalog.block_item"}
    await world.uc.update_role_matrix(admin, roles, "сужение", "k-matrix-02")
    assert mod_session.session_id in world.revoked


# ------------------------------------------------------------------------ идемпотентность admin


async def test_assign_roles_idempotency_key_replays_and_rejects_other_body(world: World) -> None:
    admin = await _admin(world)
    target = await world.make_user()
    world.events.clear()
    first = await world.uc.assign_roles(
        admin, target, frozenset({"moderator"}), "помощь", "k-1234567"
    )
    again = await world.uc.assign_roles(
        admin, target, frozenset({"moderator"}), "помощь", "k-1234567"
    )
    assert first == again
    assert "catalog.block_item" in first["perms"]
    assert len(world.events) == 1
    assert len(world.data.audit) == 1 + 1  # bootstrap admin + это назначение
    with pytest.raises(ConflictError) as info:
        await world.uc.assign_roles(admin, target, frozenset(), "другое", "k-1234567")
    assert info.value.code == "idempotency.key_reused"


async def test_matrix_update_idempotency(world: World) -> None:
    admin = await _admin(world)
    roles = {**world.data.matrix.roles, "helper": frozenset({"users.view"})}
    first = await world.uc.update_role_matrix(admin, roles, "новая роль", "m-1234567")
    assert first["roles"]["helper"] == ["users.view"]
    world.data.matrix = world.data.matrix.replace(
        {k: v for k, v in world.data.matrix.roles.items() if k != "helper"}
    )
    replay = await world.uc.update_role_matrix(admin, roles, "новая роль", "m-1234567")
    assert replay == first
    assert "helper" not in world.data.matrix.roles  # повтор не выполняет мутацию заново


# ------------------------------------------------------------------------ M2: привязка/отвязка


def _state(url: str) -> str:
    return url.split("state=", 1)[1].split("&", 1)[0]


async def _actor(world: World) -> Actor:
    user_id = await world.make_user()
    issued = await world.login("neo_2026", PASSWORD, ip=None)
    return Actor(user_id, issued.roles, issued.perms, issued.session_id)


async def test_link_requires_password_and_notifies(world: World) -> None:
    actor = await _actor(world)
    with pytest.raises(InvalidInputError) as info:
        await world.uc.start_oauth(
            world.yandex.provider, purpose="link", actor=actor, return_to=None
        )
    assert info.value.code == "auth.password_required"
    with pytest.raises(UnauthenticatedError) as bad:
        await world.uc.start_oauth(
            world.yandex.provider, purpose="link", actor=actor, return_to=None, password=WRONG
        )
    assert bad.value.code == "auth.invalid_credentials"

    started = await world.uc.start_oauth(
        world.yandex.provider, purpose="link", actor=actor, return_to=None, password=PASSWORD
    )
    world.yandex.will_return("c1", "ya-1")
    world.emails.clear()
    await world.uc.complete_oauth(
        world.yandex.provider,
        state=_state(started.authorize_url),
        code="c1",
        browser_nonce=started.browser_nonce,
        actor=actor,
        ip=None,
    )
    (mail,) = world.emails
    assert mail.purpose == "provider_linked"
    assert mail.providers == ("yandex",)
    assert mail.to.value == "neo@example.com"


async def test_link_reauth_is_rate_limited(world: World) -> None:
    actor = await _actor(world)
    for _ in range(5):
        with pytest.raises(UnauthenticatedError):
            await world.uc.start_oauth(
                world.yandex.provider, purpose="link", actor=actor, return_to=None, password=WRONG
            )
    with pytest.raises(RateLimitedError):
        await world.uc.start_oauth(
            world.yandex.provider, purpose="link", actor=actor, return_to=None, password=PASSWORD
        )


async def test_unlink_requires_password_and_notifies(world: World) -> None:
    actor = await _actor(world)
    started = await world.uc.start_oauth(
        world.yandex.provider, purpose="link", actor=actor, return_to=None, password=PASSWORD
    )
    world.yandex.will_return("c1", "ya-1")
    await world.uc.complete_oauth(
        world.yandex.provider,
        state=_state(started.authorize_url),
        code="c1",
        browser_nonce=started.browser_nonce,
        actor=actor,
        ip=None,
    )
    with pytest.raises(InvalidInputError):
        await world.uc.unlink(actor.user_id, world.yandex.provider, password=None, ip=None)
    with pytest.raises(UnauthenticatedError):
        await world.uc.unlink(actor.user_id, world.yandex.provider, password=WRONG, ip=None)
    assert await world.uc.list_linked(actor.user_id)
    world.emails.clear()
    await world.uc.unlink(actor.user_id, world.yandex.provider, password=PASSWORD, ip=None)
    assert not await world.uc.list_linked(actor.user_id)
    (mail,) = world.emails
    assert (mail.purpose, mail.providers) == ("provider_unlinked", ("yandex",))


async def test_password_reset_reports_linked_accounts_and_keeps_them(world: World) -> None:
    actor = await _actor(world)
    started = await world.uc.start_oauth(
        world.yandex.provider, purpose="link", actor=actor, return_to=None, password=PASSWORD
    )
    world.yandex.will_return("c1", "ya-1")
    await world.uc.complete_oauth(
        world.yandex.provider,
        state=_state(started.authorize_url),
        code="c1",
        browser_nonce=started.browser_nonce,
        actor=actor,
        ip=None,
    )
    resp = await world.uc.request_password_reset("neo@example.com", ip=None)
    await world.uc.reset_password(resp.challenge_id, world.last_code(), "brand new passphrase")
    assert actor.session_id in world.revoked
    mail = world.emails[-1]
    assert (mail.purpose, mail.providers) == ("password_changed", ("yandex",))
    assert await world.uc.list_linked(actor.user_id)  # привязки не удаляются


# ------------------------------------------------------------------------ M3: старые токены


async def test_new_reset_code_invalidates_previous(world: World) -> None:
    await world.make_user()
    old = await world.uc.request_password_reset("neo@example.com", ip=None)
    old_code = world.last_code()
    new = await world.uc.request_password_reset("neo@example.com", ip=None)
    new_code = world.last_code()
    assert old.challenge_id != new.challenge_id
    with pytest.raises(InvalidInputError) as info:
        await world.uc.reset_password(old.challenge_id, old_code, "brand new passphrase")
    assert info.value.code == "auth.code_invalid"
    await world.uc.reset_password(new.challenge_id, new_code, "brand new passphrase")


# ------------------------------------------------------------------------ L4: письма жертве


async def test_mail_to_one_address_is_rate_limited(make_world: MakeWorld) -> None:
    world = make_world(email_per_address=2)
    await world.make_user()  # письмо с кодом регистрации — первое на этот адрес
    world.emails.clear()
    for _ in range(5):
        await world.uc.request_password_reset("neo@example.com", ip=None)
    assert len(world.emails) == 1  # лимит 2 на адрес, одно уже ушло при регистрации

    # Регистрация с чужим занятым адресом не превращается в рассылку писем жертве.
    world.emails.clear()
    for n in range(5):
        await world.uc.register(
            RegisterCommand(
                nickname=f"spam_{n}",
                email="neo@example.com",
                password=PASSWORD,
                consents=world.config.required_consents,
            )
        )
    assert world.emails == []
    # Другие адреса не затронуты.
    await world.uc.request_password_reset("ghost@example.com", ip=None)
    await world.make_user("trinity", "trinity@example.com")
    assert [m.to.value for m in world.emails] == ["trinity@example.com"]


# ------------------------------------------------------------------------ L6: удаление аккаунта


async def test_delete_account_password_check_is_rate_limited(world: World) -> None:
    user_id = await world.make_user()
    for _ in range(5):
        with pytest.raises(UnauthenticatedError):
            await world.uc.delete_account(user_id, WRONG, ip=None)
    with pytest.raises(RateLimitedError):
        await world.uc.delete_account(user_id, PASSWORD, ip=None)
    assert world.data.users[user_id].is_active


# ------------------------------------------------------------------------ выход из админки


async def test_admin_session_revoke_is_idempotent_and_admin_only(world: World) -> None:
    await world.make_user(roles=frozenset({"user", "moderator"}))
    adm = await world.uc.admin_login("neo_2026", PASSWORD, ip=None)
    web = await world.login("neo_2026", PASSWORD, ip=None)
    await world.uc.logout(refresh_token=web.refresh_token, kind=SessionKind.ADMIN)
    assert web.session_id not in world.revoked  # чужой вид сессии не трогаем
    await world.uc.logout(refresh_token=adm.refresh_token, kind=SessionKind.ADMIN)
    await world.uc.logout(refresh_token=adm.refresh_token, kind=SessionKind.ADMIN)
    await world.uc.logout(refresh_token="unknown", kind=SessionKind.ADMIN)
    assert adm.session_id in world.revoked
    with pytest.raises(UnauthenticatedError):
        await world.uc.refresh(adm.refresh_token, kind=SessionKind.ADMIN)
