"""Коды из писем и доверенные устройства через HTTP (ADR 0041)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gv_auth import ACCESS_COOKIE
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

ORIGIN = "https://gv.test"
PASSWORD = "correct horse battery"
DEVICE_COOKIE = "gv_device"
NEW_PASSWORD = "a brand new passphrase"
CHALLENGE_KEYS = {"challenge_id", "purpose", "code_length", "expires_in_s", "resend_after_s"}


def _origin() -> dict[str, str]:
    return {"Origin": ORIGIN}


async def _login(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.post(
        "/v1/auth/login",
        json={"login": "neo_2026", "password": password},
        headers=_origin(),
    )


async def _confirm_login(
    service: Any, client: httpx.AsyncClient, challenge_id: str, code: str
) -> httpx.Response:
    return await client.post(
        "/v1/auth/login/confirm",
        json={"challenge_id": challenge_id, "code": code},
        headers=_origin(),
    )


async def _login_with_code(service: Any, client: httpx.AsyncClient) -> httpx.Response:
    """Вход с нового устройства: пароль → код из лога → подтверждение."""
    first = await _login(client)
    assert first.json()["status"] == "code_required", first.text
    challenge_id = first.json()["challenge"]["challenge_id"]
    confirmed = await _confirm_login(service, client, challenge_id, service.code_for(challenge_id))
    assert confirmed.status_code == 200, confirmed.text
    return confirmed


def _set_cookie(response: httpx.Response, name: str) -> str:
    cookies = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")]
    assert cookies, f"нет Set-Cookie {name}"
    return cookies[0]


async def test_register_returns_challenge_and_verify_opens_session_with_device(
    service: Any,
) -> None:
    challenge = await service.register(verify=False)
    assert set(challenge) == CHALLENGE_KEYS
    assert challenge["purpose"] == "register"
    assert challenge["code_length"] == 6
    assert len(service.code_for(challenge["challenge_id"])) == 6

    verified = await service.client.post(
        "/v1/auth/verify-email",
        json={
            "challenge_id": challenge["challenge_id"],
            "code": service.code_for(challenge["challenge_id"]),
        },
        headers=_origin(),
    )

    assert verified.status_code == 200, verified.text
    assert verified.json()["user"]["kind"] == "user"
    device = _set_cookie(verified, DEVICE_COOKIE).lower()
    assert "httponly" in device
    assert "samesite=lax" in device
    assert "max-age=15552000" in device  # 180 дней
    assert "path=/v1/auth" in device
    assert (await service.client.get("/v1/me")).status_code == 200


async def test_register_response_has_same_shape_for_taken_email(service: Any) -> None:
    first = await service.register(verify=False)
    taken = await service.register("morpheus", "neo@example.com", verify=False)

    assert set(first) == set(taken) == CHALLENGE_KEYS
    assert first["purpose"] == taken["purpose"]
    assert first["expires_in_s"] == taken["expires_in_s"]


async def test_login_from_new_device_requires_code_and_issues_no_session(service: Any) -> None:
    await service.register()
    other = service.client_from("203.0.113.7")

    first = await _login(other)

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "code_required"
    assert body["user"] is None and body["expires_in"] is None
    assert body["challenge"]["purpose"] == "login"
    assert ACCESS_COOKIE not in first.cookies
    assert DEVICE_COOKIE not in first.cookies

    confirmed = await _confirm_login(
        service,
        other,
        body["challenge"]["challenge_id"],
        service.code_for(body["challenge"]["challenge_id"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert _set_cookie(confirmed, DEVICE_COOKIE)
    # Это устройство теперь доверенное: следующий вход без кода.
    assert (await _login(other)).json()["status"] == "authenticated"
    await other.aclose()


async def test_wrong_code_reports_attempts_left_and_code_for_other_purpose_is_rejected(
    service: Any,
) -> None:
    await service.register()
    other = service.client_from("203.0.113.8")
    first = await _login(other)
    challenge_id = first.json()["challenge"]["challenge_id"]
    real = service.code_for(challenge_id)
    wrong = "000000" if real != "000000" else "111111"

    bad = assert_problem(
        await _confirm_login(service, other, challenge_id, wrong), 422, "auth.code_invalid"
    )
    assert bad["details"]["attempts_left"] == 4

    # Код входа не подходит к ручке подтверждения регистрации.
    mixed = await other.post(
        "/v1/auth/verify-email",
        json={"challenge_id": challenge_id, "code": real},
        headers=_origin(),
    )
    assert_problem(mixed, 422, "auth.code_invalid")
    await other.aclose()


async def test_attempts_are_exhausted_and_correct_code_no_longer_works(service: Any) -> None:
    await service.register()
    other = service.client_from("203.0.113.9")
    challenge_id = (await _login(other)).json()["challenge"]["challenge_id"]
    real = service.code_for(challenge_id)
    wrong = "000000" if real != "000000" else "111111"

    for _ in range(5):
        assert_problem(
            await _confirm_login(service, other, challenge_id, wrong), 422, "auth.code_invalid"
        )

    exhausted = assert_problem(
        await _confirm_login(service, other, challenge_id, real), 422, "auth.code_invalid"
    )
    assert exhausted["details"]["attempts_left"] == 0
    await other.aclose()


async def test_expired_code_is_gone(service: Any) -> None:
    challenge = await service.register(verify=False)
    async with service.infra.sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE email_challenges SET created_at = now() - interval '1 hour', "
                "expires_at = now() - interval '1 second'"
            )
        )

    expired = await service.client.post(
        "/v1/auth/verify-email",
        json={
            "challenge_id": challenge["challenge_id"],
            "code": service.code_for(challenge["challenge_id"]),
        },
        headers=_origin(),
    )

    assert_problem(expired, 410, "auth.code_expired")


async def test_resend_too_early_is_rate_limited_with_retry_after(service: Any) -> None:
    challenge = await service.register(verify=False)

    response = await service.client.post(
        "/v1/auth/codes/resend",
        json={"challenge_id": challenge["challenge_id"]},
        headers=_origin(),
    )

    assert_problem(response, 429, "auth.code_resend_too_early")
    assert 1 <= int(response.headers["Retry-After"]) <= 60


async def test_forgot_has_same_shape_for_unknown_and_known_email(service: Any) -> None:
    await service.register()
    known = await service.client.post(
        "/v1/auth/password/forgot", json={"email": "neo@example.com"}, headers=_origin()
    )
    unknown = await service.client.post(
        "/v1/auth/password/forgot", json={"email": "ghost@example.com"}, headers=_origin()
    )

    assert known.status_code == unknown.status_code == 202
    assert set(known.json()) == set(unknown.json()) == CHALLENGE_KEYS
    assert known.json()["purpose"] == unknown.json()["purpose"] == "reset"
    # Для несуществующего адреса письма нет, но ответ клиенту тот же.
    unknown_id = unknown.json()["challenge_id"]
    assert not [
        e
        for e in service.logs
        if e.get("event") == "email.logged" and e.get("challenge_id") == unknown_id
    ]


async def test_password_reset_signs_in_and_trusts_only_this_device(
    service: Any,
) -> None:
    await service.register()
    old_access = service.client.cookies.get(ACCESS_COOKIE)
    other = service.client_from("203.0.113.12")
    await _login_with_code(service, other)  # второе доверенное устройство
    forgot = await service.client.post(
        "/v1/auth/password/forgot", json={"email": "neo@example.com"}, headers=_origin()
    )
    challenge_id = forgot.json()["challenge_id"]

    reset = await service.client.post(
        "/v1/auth/password/reset",
        json={
            "challenge_id": challenge_id,
            "code": service.code_for(challenge_id),
            "password": NEW_PASSWORD,
        },
        headers=_origin(),
    )

    assert reset.status_code == 200, reset.text
    assert reset.json()["user"]["kind"] == "user"
    assert _set_cookie(reset, DEVICE_COOKIE)  # устройство доверено сразу
    assert (await service.client.get("/v1/me")).status_code == 200  # и сессия уже открыта
    me = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {old_access}"})
    assert_problem(me, 401, "auth.session_revoked")
    # Это устройство входит без кода, чужое — потеряло доверие.
    assert (await _login(service.client, NEW_PASSWORD)).json()["status"] == "authenticated"
    assert (await _login(other, NEW_PASSWORD)).json()["status"] == "code_required"
    await other.aclose()


async def test_password_change_keeps_current_session_and_revokes_others(service: Any) -> None:
    await service.register()
    other = service.client_from("203.0.113.10")
    await _login_with_code(service, other)
    other_access = other.cookies.get(ACCESS_COOKIE)
    assert (await other.get("/v1/me")).status_code == 200

    start = await service.client.post(
        "/v1/auth/password/change",
        json={"current_password": PASSWORD},
        headers=service.csrf(),
    )
    assert start.status_code == 202, start.text
    assert start.json()["purpose"] == "password_change"
    challenge_id = start.json()["challenge_id"]
    done = await service.client.post(
        "/v1/auth/password/change/confirm",
        json={
            "challenge_id": challenge_id,
            "code": service.code_for(challenge_id),
            "new_password": NEW_PASSWORD,
        },
        headers=service.csrf(),
    )

    assert done.status_code == 204, done.text
    assert _set_cookie(done, DEVICE_COOKIE)  # текущее устройство закреплено
    assert (await service.client.get("/v1/me")).status_code == 200  # текущая сессия жива
    revoked = await other.get("/v1/me", headers={"Authorization": f"Bearer {other_access}"})
    assert_problem(revoked, 401, "auth.session_revoked")
    # Текущее устройство осталось доверенным, чужое — нет.
    assert (await _login(service.client, NEW_PASSWORD)).json()["status"] == "authenticated"
    assert (await _login(other, NEW_PASSWORD)).json()["status"] == "code_required"
    await other.aclose()


async def test_password_change_requires_current_password(service: Any) -> None:
    await service.register()

    response = await service.client.post(
        "/v1/auth/password/change",
        json={"current_password": "wrong password!"},
        headers=service.csrf(),
    )

    assert_problem(response, 401, "auth.invalid_credentials")


async def test_logout_all_keeps_current_device_trusted_but_not_others(service: Any) -> None:
    await service.register()
    other = service.client_from("203.0.113.11")
    await _login_with_code(service, other)

    response = await service.client.post("/v1/auth/logout-all", headers=service.csrf())
    assert response.status_code == 204, response.text

    assert (await _login(service.client)).json()["status"] == "authenticated"
    assert (await _login(other)).json()["status"] == "code_required"
    await other.aclose()
