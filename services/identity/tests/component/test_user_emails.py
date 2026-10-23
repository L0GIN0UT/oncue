"""POST /internal/v1/users/emails: доступ, фильтрация, лимиты, отсутствие адресов в логах."""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

import jwt
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from gv_auth import ACCESS_COOKIE
from gv_core.ids import uuid7
from gv_testkit import assert_problem

from .conftest import (
    GAME_SECRET,
    NOTIFICATIONS_SECRET,
    PASSWORD,
    SecretStr,
    pem,
    service_token_headers,
)

pytestmark = pytest.mark.component

URL = "/internal/v1/users/emails"
ISSUER = "https://identity.test"
FORGE_KID = "forge"


async def _user_id(service: Any, email: str) -> UUID:
    async with service.infra.sessions() as session:
        row = await session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        return row.scalar_one()  # type: ignore[no-any-return]  # Any из text()


async def _notifier(service: Any) -> dict[str, str]:
    return await service_token_headers(service.client, "notifications", NOTIFICATIONS_SECRET)


async def _post(client: Any, headers: dict[str, str], ids: list[Any] | None = None) -> Any:
    body = {"user_ids": [str(i) for i in (ids if ids is not None else [uuid7()])]}
    return await client.post(URL, json=body, headers=headers)


async def test_returns_only_verified_active_users(service: Any) -> None:
    await service.register("neo_2026", "neo@example.com")
    await service.register("trin_2026", "trin@example.com", verify=False)
    ok, unverified = (
        await _user_id(service, "neo@example.com"),
        await _user_id(service, "trin@example.com"),
    )
    headers = await _notifier(service)
    response = await service.client.post(
        URL,
        json={"user_ids": [str(ok), str(ok), str(unverified), str(uuid7())]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"items": [{"user_id": str(ok), "email": "neo@example.com"}]}
    # аудит: числа, client_id и jti; адресов нет
    audit = [e for e in service.logs if str(e["event"]).startswith("users.")]
    assert "neo@example.com" not in repr(audit)
    read = next(e for e in audit if e["event"] == "users.emails_read")
    jti = jwt.decode(headers["Authorization"].split()[1], options={"verify_signature": False})[
        "jti"
    ]
    assert (read["client_id"], read["token_id"]) == ("notifications", jti)
    assert (read["requested"], read["returned"]) == (3, 1)


async def test_account_deleted_via_use_case_is_skipped(service: Any) -> None:
    await service.register()
    await service.login()
    uid = await _user_id(service, "neo@example.com")
    deleted = await service.client.request(
        "DELETE", "/v1/me", json={"password": PASSWORD}, headers=service.csrf()
    )
    assert deleted.status_code == 204
    response = await _post(service.client, await _notifier(service), [uid])
    assert response.json() == {"items": []}


async def test_tombstone_email_is_skipped_even_if_status_active(service: Any) -> None:
    """Второй рубеж: рассинхрон (status=active, e-mail уже обезличен) адрес не отдаёт."""
    await service.register()
    uid = await _user_id(service, "neo@example.com")
    async with service.infra.sessions() as session, session.begin():
        await session.execute(
            text("UPDATE users SET email = :e WHERE id = :i"),
            {"e": f"{uid.hex}@deleted.invalid", "i": uid},
        )
    response = await _post(service.client, await _notifier(service), [uid])
    assert response.json() == {"items": []}


async def test_no_token_is_401(service: Any) -> None:
    response = await service.client.post(URL, json={"user_ids": [str(uuid7())]})
    assert_problem(response, 401, "auth.unauthenticated")


async def test_user_token_is_rejected(service: Any) -> None:
    await service.register()
    headers = {"Authorization": f"Bearer {service.client.cookies.get(ACCESS_COOKIE)}"}
    assert_problem(await _post(service.client, headers), 401, "auth.token_invalid")


async def test_service_token_without_scope_is_403(service: Any) -> None:
    response = await _post(service.client, service.admin_client)
    assert_problem(response, 403, "auth.forbidden")


async def test_token_for_other_audience_is_rejected(service: Any) -> None:
    headers = await service_token_headers(service.client, "game", GAME_SECRET, "media_pipeline")
    assert_problem(await _post(service.client, headers), 401, "auth.token_invalid")


@pytest.mark.parametrize(
    "body",
    [
        {"user_ids": []},
        {"user_ids": ["not-a-uuid"]},
        {"user_ids": [str(uuid7())] * 501},
        {"user_ids": [str(uuid7())], "extra": 1},
        {},
    ],
)
async def test_body_is_validated(service: Any, body: dict[str, Any]) -> None:
    response = await service.client.post(URL, json=body, headers=await _notifier(service))
    assert response.status_code == 422


# ---------------------------------------------------------------- подделанные токены


def _service_claims(**over: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "identity",
        "sub": "svc:notifications",
        "scope": "users.emails.read",
        "jti": str(uuid7()),
        "iat": now,
        "exp": now + 600,
    }
    claims.update(over)
    return claims


def _user_claims(*, aud: str, guest: bool = False) -> dict[str, Any]:
    claims = _service_claims(
        aud=aud,
        sub=str(uuid7()),
        roles=["guest"] if guest else ["admin"],
        perms=[] if guest else ["admin.access", "users.emails.read"],
        sid=str(uuid7()),
    )
    del claims["scope"]
    if guest:
        claims["gst"] = True
    return claims


def _sign(claims: dict[str, Any], private_pem: str, *, alg: str = "EdDSA") -> str:
    key: Any = None if alg == "none" else private_pem
    token = jwt.encode(claims, key, algorithm=alg, headers={"kid": FORGE_KID})
    return f"Bearer {token}"


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("admin_user_aud_guessverse", 401, "auth.token_invalid"),
        ("admin_user_aud_identity", 401, "auth.token_invalid"),
        ("guest_aud_guessverse", 401, "auth.token_invalid"),
        ("guest_aud_identity", 401, "auth.token_invalid"),
        ("expired_service", 401, "auth.token_expired"),
        ("other_key", 401, "auth.token_invalid"),
        ("alg_none", 401, "auth.token_invalid"),
        ("no_scope", 403, "auth.forbidden"),
        ("valid", 200, None),
    ],
)
async def test_forged_tokens(seeding: Any, case: str, status: int, code: str | None) -> None:
    own = pem()
    keys = [{"kid": FORGE_KID, "private_pem": own, "active": True}]
    settings, prefix = seeding.settings(jwt={"signing_keys": SecretStr(json.dumps(keys))})
    now = int(time.time())
    tokens: dict[str, str] = {
        "admin_user_aud_guessverse": _sign(_user_claims(aud="guessverse"), own),
        "admin_user_aud_identity": _sign(_user_claims(aud="identity"), own),
        "guest_aud_guessverse": _sign(_user_claims(aud="guessverse", guest=True), own),
        "guest_aud_identity": _sign(_user_claims(aud="identity", guest=True), own),
        "expired_service": _sign(_service_claims(iat=now - 900, exp=now - 300), own),
        "other_key": _sign(_service_claims(), pem()),
        "alg_none": _sign(_service_claims(), own, alg="none"),
        "no_scope": _sign(_service_claims(scope="admin_sessions"), own),
        "valid": _sign(_service_claims(), own),
    }
    async with seeding.app(settings, prefix) as (client, _admin):
        response = await _post(client, {"Authorization": tokens[case]})
    if code is None:
        assert response.status_code == status, response.text
    else:
        assert_problem(response, status, code)


# ---------------------------------------------------------------- лимиты


async def test_call_rate_limit_per_client(seeding: Any) -> None:
    settings, prefix = seeding.settings(users_emails_limit={"per_client": 2, "window_s": 60})
    async with seeding.app(settings, prefix) as (client, _admin):
        headers = await service_token_headers(client, "notifications", NOTIFICATIONS_SECRET)
        for _ in range(2):
            assert (await _post(client, headers)).status_code == 200
        response = await _post(client, headers)
        assert_problem(response, 429, "rate.limited")
        assert int(response.headers["retry-after"]) >= 1


async def test_ids_window_is_weighted_by_address_count(seeding: Any) -> None:
    settings, prefix = seeding.settings(users_emails_limit={"ids_per_hour": 5})
    async with seeding.app(settings, prefix) as (client, _admin):
        headers = await service_token_headers(client, "notifications", NOTIFICATIONS_SECRET)
        assert (await _post(client, headers, [uuid7() for _ in range(4)])).status_code == 200
        # 4 + 2 > 5: даже один вызов не по числу обращений, а по числу адресов
        response = await _post(client, headers, [uuid7(), uuid7()])
        assert_problem(response, 429, "rate.limited")
        assert int(response.headers["retry-after"]) >= 1


async def test_redis_down_is_fail_closed_503(service: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await _notifier(service)

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(service.infra.redis, "pipeline", broken)
    response = await _post(service.client, headers)
    assert_problem(response, 503, "rate.limiter_unavailable")
    assert int(response.headers["retry-after"]) >= 1


async def test_redis_down_does_not_close_login(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Лимитеры входа остаются fail-open: сбой Redis не отключает вход."""
    await service.register()
    service.forget_session()

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RedisConnectionError("redis down")

    monkeypatch.setattr(service.infra.redis, "pipeline", broken)
    assert (await service.login()).status_code == 200
