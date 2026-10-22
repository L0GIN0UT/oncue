"""Коды из писем и доверенные устройства (ADR 0041): сценарии use-cases на фейках."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from identity_service.application.config import CodeConfig

from gv_core import (
    GoneError,
    InvalidInputError,
    RateLimitedError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

PASSWORD = "correct horse battery"
NEW_PASSWORD = "brand new passphrase"
STRANGER = "stranger-device"


def _world_with(make_world: Any, **codes: Any) -> Any:
    world = make_world()
    return type(world)(dataclasses.replace(world.config, codes=CodeConfig(**codes)))


async def _new_device_login(world: Any, login: str = "neo_2026") -> Any:
    result = await world.login_result(login, PASSWORD, device_token=STRANGER)
    assert result.status == "code_required"
    return result.challenge


# ------------------------------------------------------------------------ вход с нового устройства


async def test_unknown_device_gets_code_and_no_session(world: Any) -> None:
    await world.make_user()
    sessions_before = set(world.data.sessions)
    world.emails.clear()
    challenge = await _new_device_login(world)
    assert challenge.purpose == "login"
    assert set(world.data.sessions) == sessions_before  # сессии нет до подтверждения
    (mail,) = world.emails
    assert mail.purpose == "code_login"
    assert mail.code


async def test_login_without_device_cookie_requires_code(world: Any) -> None:
    await world.make_user()
    result = await world.login_result("neo_2026", PASSWORD, device_token=None)
    assert result.status == "code_required"
    assert result.session is None


async def test_confirm_login_opens_session_and_trusts_device(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    verified = await world.uc.confirm_login(challenge.challenge_id, world.last_code())
    assert verified.session.session_id in world.data.sessions
    again = await world.login_result("neo_2026", PASSWORD, device_token=verified.device_token)
    assert again.status == "authenticated"


async def test_planted_device_cookie_is_never_trusted(world: Any) -> None:
    """Подсаженный атакующим gv_device не становится доверенным, когда жертва вводит код."""
    await world.make_user()
    challenge = await _new_device_login(world)  # запрос пришёл с cookie STRANGER

    verified = await world.uc.confirm_login(challenge.challenge_id, world.last_code())

    assert verified.device_token != STRANGER  # сервер выдал свой токен
    planted = await world.login_result("neo_2026", PASSWORD, device_token=STRANGER)
    assert planted.status == "code_required"


async def test_new_device_gets_fresh_token_when_it_had_no_cookie(world: Any) -> None:
    await world.make_user()
    result = await world.login_result("neo_2026", PASSWORD, device_token=None)
    verified = await world.uc.confirm_login(result.challenge.challenge_id, world.last_code())
    assert verified.device_token
    again = await world.login_result("neo_2026", PASSWORD, device_token=verified.device_token)
    assert again.status == "authenticated"


async def test_trusted_device_logs_in_without_mail(world: Any) -> None:
    await world.make_user()
    world.emails.clear()
    result = await world.login_result("neo_2026", PASSWORD, device_token="device-token-known")
    assert result.status == "authenticated"
    assert world.emails == []


async def test_wrong_password_on_new_device_sends_nothing(world: Any) -> None:
    await world.make_user()
    world.emails.clear()
    with pytest.raises(UnauthenticatedError):
        await world.login_result("neo_2026", "wrong password!", device_token=STRANGER)
    assert world.emails == []


async def test_device_trust_expires(world: Any) -> None:
    await world.make_user()
    world.clock.advance(seconds=world.config.codes.device_ttl_s + 1)
    result = await world.login_result("neo_2026", PASSWORD, device_token="device-token-known")
    assert result.status == "code_required"


async def test_trust_is_per_user(world: Any) -> None:
    await world.make_user()
    await world.make_user("trinity", "trinity@example.com")
    # Устройство доверено обоим (одна cookie в тестовом мире), но чужому оно не подходит.
    result = await world.login_result("trinity", PASSWORD, device_token=STRANGER)
    assert result.status == "code_required"


async def test_new_login_code_cancels_previous_one(world: Any) -> None:
    await world.make_user()
    first = await _new_device_login(world)
    first_code = world.last_code()
    second = await _new_device_login(world)
    assert first.challenge_id != second.challenge_id
    with pytest.raises(InvalidInputError) as info:
        await world.uc.confirm_login(first.challenge_id, first_code)
    assert info.value.code == "auth.code_invalid"
    await world.uc.confirm_login(second.challenge_id, world.last_code())


async def test_login_code_cannot_confirm_registration(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    with pytest.raises(InvalidInputError) as info:
        await world.uc.verify_email(challenge.challenge_id, world.last_code())
    assert info.value.code == "auth.code_invalid"


async def test_login_code_request_is_rate_limited_per_ip(make_world: Any) -> None:
    world = _world_with(make_world, request_per_ip_per_min=2)
    await world.make_user()
    for _ in range(2):
        await world.login_result("neo_2026", PASSWORD, ip="9.9.9.9", device_token=STRANGER)
    with pytest.raises(RateLimitedError):
        await world.login_result("neo_2026", PASSWORD, ip="9.9.9.9", device_token=STRANGER)


# ------------------------------------------------------------------------ попытки и лимиты


async def test_wrong_code_reports_attempts_left_and_keeps_spent_attempt(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    for left in (4, 3):
        with pytest.raises(InvalidInputError) as info:
            await world.uc.confirm_login(challenge.challenge_id, "999999")
        assert info.value.code == "auth.code_invalid"
        assert info.value.details["attempts_left"] == left
    # Откат транзакции запроса не возвращает попытки.
    assert world.challenge(challenge.challenge_id).attempts == 2


async def test_exhausted_attempts_block_even_the_right_code(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    code = world.last_code()
    for _ in range(5):
        with pytest.raises(InvalidInputError):
            await world.uc.confirm_login(challenge.challenge_id, "999999")
    with pytest.raises(InvalidInputError) as info:
        await world.uc.confirm_login(challenge.challenge_id, code)
    assert info.value.details["attempts_left"] == 0
    assert world.challenge(challenge.challenge_id).consumed_at is None


async def test_correct_code_is_single_use(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    code = world.last_code()
    await world.uc.confirm_login(challenge.challenge_id, code)
    with pytest.raises(InvalidInputError):
        await world.uc.confirm_login(challenge.challenge_id, code)


async def test_unknown_challenge_is_invalid(world: Any) -> None:
    from gv_core.ids import uuid7

    with pytest.raises(InvalidInputError) as info:
        await world.uc.confirm_login(uuid7(), "123456")
    assert info.value.code == "auth.code_invalid"


async def test_expired_code(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    world.clock.advance(seconds=600)
    with pytest.raises(GoneError) as info:
        await world.uc.confirm_login(challenge.challenge_id, world.last_code())
    assert info.value.code == "auth.code_expired"


async def test_daily_failure_cap(make_world: Any) -> None:
    world = _world_with(make_world, daily_fail_limit=3, max_attempts=10)
    await world.make_user()
    challenge = await _new_device_login(world)
    for _ in range(3):
        with pytest.raises(InvalidInputError):
            await world.uc.confirm_login(challenge.challenge_id, "999999")
    with pytest.raises(RateLimitedError) as info:
        await world.uc.confirm_login(challenge.challenge_id, world.last_code())
    assert info.value.code == "auth.code_limit_exceeded"
    # Пароль и доверенные устройства при этом работают.
    assert (
        await world.login_result("neo_2026", PASSWORD, device_token="device-token-known")
    ).session
    # Через сутки потолок снимается.
    world.clock.advance(hours=24, seconds=1)
    fresh = await _new_device_login(world)
    await world.uc.confirm_login(fresh.challenge_id, world.last_code())


async def test_confirm_is_rate_limited_per_ip(make_world: Any) -> None:
    world = _world_with(make_world, confirm_per_ip_per_min=2, max_attempts=10)
    await world.make_user()
    challenge = await _new_device_login(world)
    for _ in range(2):
        with pytest.raises(InvalidInputError):
            await world.uc.confirm_login(challenge.challenge_id, "999999", ip="8.8.8.8")
    with pytest.raises(RateLimitedError) as info:
        await world.uc.confirm_login(challenge.challenge_id, world.last_code(), ip="8.8.8.8")
    assert info.value.code == "rate.limited"
    assert world.challenge(challenge.challenge_id).attempts == 2  # отказ по IP попытку не тратит


async def test_code_length_comes_from_config(make_world: Any) -> None:
    world = _world_with(make_world, length=8)
    await world.make_user()
    challenge = await _new_device_login(world)
    assert challenge.code_length == 8
    assert len(world.last_code()) == 8


# ------------------------------------------------------------------------ повторная отправка


async def test_resend_too_early_then_replaces_code(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    old_code = world.last_code()
    world.advance(10)
    with pytest.raises(RateLimitedError) as info:
        await world.uc.resend_code(challenge.challenge_id)
    assert info.value.code == "auth.code_resend_too_early"
    assert info.value.details["retry_after"] == 50

    with pytest.raises(InvalidInputError):
        await world.uc.confirm_login(challenge.challenge_id, "999999")  # тратим попытку
    world.advance(50)
    resent = await world.uc.resend_code(challenge.challenge_id)
    assert resent.challenge_id == challenge.challenge_id
    stored = world.challenge(challenge.challenge_id)
    assert (stored.send_count, stored.attempts) == (2, 0)
    new_code = world.last_code()
    assert new_code != old_code
    with pytest.raises(InvalidInputError):
        await world.uc.confirm_login(challenge.challenge_id, old_code)
    await world.uc.confirm_login(challenge.challenge_id, new_code)


async def test_resend_after_expiry_issues_new_challenge(world: Any) -> None:
    await world.make_user()
    challenge = await _new_device_login(world)
    world.advance(601)
    resent = await world.uc.resend_code(challenge.challenge_id)
    assert resent.challenge_id != challenge.challenge_id
    assert resent.purpose == "login"
    await world.uc.confirm_login(resent.challenge_id, world.last_code())


async def test_resend_consumed_or_unknown_is_invalid(world: Any) -> None:
    from gv_core.ids import uuid7

    await world.make_user()
    challenge = await _new_device_login(world)
    await world.uc.confirm_login(challenge.challenge_id, world.last_code())
    world.advance(120)
    for challenge_id in (challenge.challenge_id, uuid7()):
        with pytest.raises(InvalidInputError) as info:
            await world.uc.resend_code(challenge_id)
        assert info.value.code == "auth.code_invalid"


async def test_resend_registration_code_verifies_account(world: Any) -> None:
    from identity_service.application.dto import RegisterCommand

    resp = await world.uc.register(
        RegisterCommand("neo_2026", "neo@example.com", PASSWORD, world.config.required_consents)
    )
    world.advance(61)
    await world.uc.resend_code(resp.challenge_id)
    assert world.emails[-1].purpose == "code_register"
    result = await world.uc.verify_email(resp.challenge_id, world.last_code())
    assert result.session.user_id in world.data.users


# ------------------------------------------------------------------------ пустышки


async def test_decoy_behaves_like_real_challenge(world: Any) -> None:
    await world.make_user()
    real = await world.uc.request_password_reset("neo@example.com", ip=None)
    ghost = await world.uc.request_password_reset("ghost@example.com", ip=None)
    for resp in (real, ghost):
        with pytest.raises(InvalidInputError) as info:
            await world.uc.reset_password(resp.challenge_id, "999999", NEW_PASSWORD)
        assert info.value.details["attempts_left"] == 4
    stored = world.data.challenges[ghost.challenge_id]
    assert stored.user_id is None


async def test_decoy_code_is_never_mailed_and_resend_sends_nothing(world: Any) -> None:
    await world.make_user()
    world.emails.clear()
    ghost = await world.uc.request_password_reset("ghost@example.com", ip=None)
    world.advance(61)
    await world.uc.resend_code(ghost.challenge_id)
    assert world.emails == []


async def test_suppressed_mail_creates_no_challenge_but_same_shape(make_world: Any) -> None:
    world = make_world(email_per_address=1)
    await world.make_user()  # письмо регистрации израсходовало лимит адреса
    world.emails.clear()
    before = set(world.data.challenges)
    real = await world.uc.request_password_reset("neo@example.com", ip=None)
    assert world.emails == []
    assert set(world.data.challenges) == before
    assert (real.purpose, real.code_length) == ("reset", 6)
    with pytest.raises(InvalidInputError):  # фиктивный id ведёт себя как неверный код
        await world.uc.reset_password(real.challenge_id, "123456", NEW_PASSWORD)


async def test_suppressed_login_mail_gives_code_required_without_challenge(make_world: Any) -> None:
    world = make_world(email_per_address=1)
    await world.make_user()
    before = set(world.data.challenges)
    result = await world.login_result("neo_2026", PASSWORD, device_token=STRANGER)
    assert result.status == "code_required"
    assert set(world.data.challenges) == before
    assert world.emails[-1].code is not None and len(world.codes_sent()) == 1  # только регистрация


# ------------------------------------------------------------------------ сброс пароля


async def test_reset_revokes_all_sessions_and_all_trusted_devices(world: Any) -> None:
    await world.make_user()
    session = await world.login("neo_2026", PASSWORD)
    resp = await world.uc.request_password_reset("neo@example.com", ip=None)
    await world.uc.reset_password(resp.challenge_id, world.last_code(), NEW_PASSWORD)
    assert session.session_id in world.revoked
    result = await world.login_result("neo_2026", NEW_PASSWORD, device_token="device-token-known")
    assert result.status == "code_required"  # доверие снято и у "своего" устройства
    assert world.emails[-1].purpose == "code_login"


async def test_reset_with_weak_password_keeps_code_usable(world: Any) -> None:
    await world.make_user()
    resp = await world.uc.request_password_reset("neo@example.com", ip=None)
    code = world.last_code()
    with pytest.raises(InvalidInputError) as info:
        await world.uc.reset_password(resp.challenge_id, code, "short")
    assert info.value.code == "auth.password_weak"
    assert world.challenge(resp.challenge_id).consumed_at is None
    await world.uc.reset_password(resp.challenge_id, code, NEW_PASSWORD)


# ------------------------------------------------------------------------ смена пароля


async def test_password_change_requires_current_password(world: Any) -> None:
    user_id = await world.make_user()
    world.emails.clear()
    with pytest.raises(UnauthenticatedError) as info:
        await world.uc.request_password_change(user_id, "wrong password!", session_id=None, ip=None)
    assert info.value.code == "auth.invalid_credentials"
    with pytest.raises(InvalidInputError) as missing:
        await world.uc.request_password_change(user_id, None, session_id=None, ip=None)
    assert missing.value.code == "auth.password_required"
    assert world.emails == []


async def test_password_change_revokes_other_sessions_and_devices_but_not_current(
    world: Any,
) -> None:
    user_id = await world.make_user()
    current = await world.login("neo_2026", PASSWORD)
    other = await world.login("neo_2026", PASSWORD)
    # Второе устройство тоже доверено.
    challenge = await _new_device_login(world)
    other_device = (
        await world.uc.confirm_login(challenge.challenge_id, world.last_code())
    ).device_token

    resp = await world.uc.request_password_change(
        user_id, PASSWORD, session_id=current.session_id, ip=None
    )
    assert resp.purpose == "password_change"
    assert world.emails[-1].purpose == "code_password_change"
    new_token = await world.uc.confirm_password_change(
        user_id,
        resp.challenge_id,
        world.last_code(),
        NEW_PASSWORD,
        session_id=current.session_id,
    )
    assert world.data.creds[user_id] == f"h${NEW_PASSWORD}"
    assert other.session_id in world.revoked
    assert current.session_id not in world.revoked
    # Текущее устройство получило новый токен; прежние (в том числе его старый) сняты.
    assert (await world.login_result("neo_2026", NEW_PASSWORD, device_token=new_token)).session
    old = await world.login_result("neo_2026", NEW_PASSWORD, device_token="device-token-known")
    assert old.status == "code_required"
    stranger = await world.login_result("neo_2026", NEW_PASSWORD, device_token=other_device)
    assert stranger.status == "code_required"
    assert world.emails[-1].purpose == "code_login"


async def test_password_change_code_of_other_user_is_rejected(world: Any) -> None:
    alice = await world.make_user()
    bob = await world.make_user("trinity", "trinity@example.com")
    resp = await world.uc.request_password_change(alice, PASSWORD, session_id=None, ip=None)
    with pytest.raises(InvalidInputError) as info:
        await world.uc.confirm_password_change(
            bob, resp.challenge_id, world.last_code(), NEW_PASSWORD, session_id=None
        )
    assert info.value.code == "auth.code_invalid"
    assert world.data.creds[bob] == f"h${PASSWORD}"


async def test_password_change_weak_password_rejected(world: Any) -> None:
    user_id = await world.make_user()
    resp = await world.uc.request_password_change(user_id, PASSWORD, session_id=None, ip=None)
    with pytest.raises(InvalidInputError) as info:
        await world.uc.confirm_password_change(
            user_id, resp.challenge_id, world.last_code(), "short", session_id=None
        )
    assert info.value.code == "auth.password_weak"
    assert world.data.creds[user_id] == f"h${PASSWORD}"


# ------------------------------------------------------------------------ logout-all, удаление


async def test_logout_all_keeps_trust_of_current_device_only(world: Any) -> None:
    user_id = await world.make_user()
    challenge = await _new_device_login(world)
    verified = await world.uc.confirm_login(challenge.challenge_id, world.last_code())
    await world.uc.logout_all(user_id, device_token=verified.device_token)
    kept = await world.login_result("neo_2026", PASSWORD, device_token=verified.device_token)
    assert kept.session
    other = await world.login_result("neo_2026", PASSWORD, device_token="device-token-known")
    assert other.status == "code_required"


async def test_delete_account_drops_device_trust(world: Any) -> None:
    user_id = await world.make_user()
    await world.uc.delete_account(user_id, PASSWORD, ip=None)
    assert not [d for d in world.data.devices.values() if d.user_id == user_id]


# ------------------------------------------------------------------------ admin


async def test_admin_login_does_not_need_device(world: Any) -> None:
    await world.make_user(roles=frozenset({"user", "moderator"}))
    issued = await world.uc.admin_login("neo_2026", PASSWORD, ip=None)
    assert "admin.access" in issued.perms


async def test_code_mails_carry_challenge_id(world: Any) -> None:
    await world.make_user()
    first = await _new_device_login(world)
    assert world.emails[-1].challenge_id == first.challenge_id
    world.advance(61)
    await world.uc.resend_code(first.challenge_id)
    assert world.emails[-1].challenge_id == first.challenge_id
    reset = await world.uc.request_password_reset("neo@example.com", ip=None)
    assert world.emails[-1].challenge_id == reset.challenge_id


# ------------------------------------------------------------------------ вход сразу после смены


async def test_reset_opens_session_and_trusts_the_resetting_device(world: Any) -> None:
    await world.make_user()
    old = await world.login("neo_2026", PASSWORD)
    resp = await world.uc.request_password_reset("neo@example.com", ip=None)

    verified = await world.uc.reset_password(resp.challenge_id, world.last_code(), NEW_PASSWORD)

    assert verified.device_token
    assert verified.session.access_token
    assert old.session_id in world.revoked  # прежние сессии отозваны
    assert verified.session.session_id not in world.revoked  # а новая — нет
    again = await world.login_result("neo_2026", NEW_PASSWORD, device_token=verified.device_token)
    assert again.status == "authenticated"  # устройство доверено — кода не просит
    known = await world.login_result("neo_2026", NEW_PASSWORD, device_token="device-token-known")
    assert known.status == "code_required"  # прежние устройства доверие потеряли


async def test_reset_without_device_cookie_issues_a_new_device_token(world: Any) -> None:
    await world.make_user()
    resp = await world.uc.request_password_reset("neo@example.com", ip=None)

    verified = await world.uc.reset_password(resp.challenge_id, world.last_code(), NEW_PASSWORD)

    assert verified.device_token and verified.device_token != "device-token-known"
    again = await world.login_result("neo_2026", NEW_PASSWORD, device_token=verified.device_token)
    assert again.status == "authenticated"


async def test_password_change_trusts_current_device_even_without_cookie(world: Any) -> None:
    user_id = await world.make_user()
    current = await world.login("neo_2026", PASSWORD)
    resp = await world.uc.request_password_change(
        user_id, PASSWORD, session_id=current.session_id, ip=None
    )

    token = await world.uc.confirm_password_change(
        user_id,
        resp.challenge_id,
        world.last_code(),
        NEW_PASSWORD,
        session_id=current.session_id,
    )

    assert token and token != "device-token-known"
    again = await world.login_result("neo_2026", NEW_PASSWORD, device_token=token)
    assert again.status == "authenticated"


# ------------------------------------------------------------------------ ревью безопасности


async def test_resend_cannot_keep_a_record_alive_past_the_daily_window(world: Any) -> None:
    """Запись нельзя держать живой повторными отправками, пока она не выпадет из окна потолка.

    ``failures_since`` считает записи по ``created_at``; без абсолютного срока жизни
    атакующий дождался бы суток, и неудачи старой записи перестали бы учитываться.
    """
    await world.make_user()
    cid = (await _new_device_login(world)).challenge_id
    for _ in range(190):  # ≈ 26 часов с шагом 500 с (TTL кода — 600 с)
        world.advance(seconds=500)
        cid = (await world.uc.resend_code(cid)).challenge_id

    with pytest.raises(RateLimitedError) as info:
        for _ in range(40):
            try:
                await world.uc.confirm_login(cid, "999999")
            except InvalidInputError as exc:
                if exc.details["attempts_left"] == 0:
                    world.advance(seconds=61)
                    cid = (await world.uc.resend_code(cid)).challenge_id
    assert info.value.code == "auth.code_limit_exceeded"


async def test_resend_after_max_lifetime_issues_a_new_record(world: Any) -> None:
    await world.make_user()
    first = (await _new_device_login(world)).challenge_id
    cid = first
    for _ in range(8):  # 8 × 500 с > 3600 с (max_lifetime_s по умолчанию)
        world.advance(seconds=500)
        cid = (await world.uc.resend_code(cid)).challenge_id
    assert cid != first
    assert world.challenge(first).cancelled_at is not None


async def test_decoy_behaves_like_a_real_code_for_repeated_requests(world: Any) -> None:
    """Повторный запрос отменяет прежнюю пустышку так же, как настоящий код."""
    await world.make_user()
    for address in ("neo@example.com", "ghost@example.com"):
        first = await world.uc.request_password_reset(address, ip=None)
        await world.uc.request_password_reset(address, ip=None)
        with pytest.raises(InvalidInputError) as info:
            await world.uc.reset_password(first.challenge_id, "000000", NEW_PASSWORD)
        assert info.value.details["attempts_left"] == 0, address


async def test_daily_cap_applies_to_decoys_too(world: Any) -> None:
    for _ in range(4):
        resp = await world.uc.request_password_reset("ghost@example.com", ip=None)
        for _ in range(5):
            with pytest.raises(InvalidInputError):
                await world.uc.reset_password(resp.challenge_id, "000000", NEW_PASSWORD)
    resp = await world.uc.request_password_reset("ghost@example.com", ip=None)
    with pytest.raises(RateLimitedError) as info:
        await world.uc.reset_password(resp.challenge_id, "000000", NEW_PASSWORD)
    assert info.value.code == "auth.code_limit_exceeded"


async def test_delete_account_removes_codes_bound_to_the_address_and_user(world: Any) -> None:
    user_id = await world.make_user()
    await world.uc.request_password_reset("neo@example.com", ip=None)
    await _new_device_login(world)
    assert world.data.challenges

    await world.uc.delete_account(user_id, PASSWORD, ip=None)

    assert not [c for c in world.data.challenges.values() if c.user_id == user_id]
    assert not world.data.challenges  # и коды по хэшу старого адреса тоже


class _FakePurger:
    def __init__(self, batches: list[int]) -> None:
        self.batches, self.calls = list(batches), []

    async def purge(self, **kwargs: Any) -> int:
        self.calls.append(kwargs)
        return self.batches.pop(0)


async def test_purge_batches_until_short_batch_and_uses_retention() -> None:
    from datetime import timedelta

    from identity_service.application.commands.challenge_purge import PurgeEmailChallenges

    from gv_testkit import FakeClock

    clock = FakeClock()
    config = CodeConfig(purge_batch=10, purge_max_batches=5, purge_retention_s=7200,
                        device_revoked_retention_s=600)  # fmt: skip
    purger = _FakePurger([10, 10, 3, 10])

    assert await PurgeEmailChallenges(purger, clock, config)() == 23
    first = purger.calls[0]
    assert first["challenges_before"] == clock.now() - timedelta(seconds=7200)
    assert first["devices_revoked_before"] == clock.now() - timedelta(seconds=600)
    assert first["limit"] == 10 and len(purger.calls) == 3
