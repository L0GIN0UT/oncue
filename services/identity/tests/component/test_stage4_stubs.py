"""Контракт шага 0a этапа 4 (§2.5, D9, I1): ``POST /v1/realtime/token`` объявлен (501 до потока I).

Авторизация — до заглушки: без сессии 401, гость 403 ``auth.guest_forbidden``, cookie-запрос
без CSRF или с чужим Origin — 403 ``auth.csrf_failed``.
"""

from __future__ import annotations

from typing import Any

import pytest

from gv_auth import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

ORIGIN = "https://gv.test"
PATH = "/v1/realtime/token"


def _csrf(service: Any) -> dict[str, str]:
    return {"Origin": ORIGIN, CSRF_HEADER: service.client.cookies.get(CSRF_COOKIE) or ""}


async def test_realtime_token_without_session_is_401(service: Any) -> None:
    assert_problem(
        await service.client.post(PATH, headers={"Origin": ORIGIN}), 401, "auth.unauthenticated"
    )


async def test_realtime_token_forbidden_for_guest(service: Any) -> None:
    started = await service.client.post("/v1/auth/guest", headers={"Origin": ORIGIN})
    assert started.status_code == 204, started.text

    assert_problem(
        await service.client.post(PATH, headers=_csrf(service)), 403, "auth.guest_forbidden"
    )
    bearer = {"Authorization": f"Bearer {service.client.cookies.get(ACCESS_COOKIE)}"}
    assert_problem(await service.client.post(PATH, headers=bearer), 403, "auth.guest_forbidden")


async def test_realtime_token_for_user_checks_origin_and_csrf_then_stub(service: Any) -> None:
    await service.register()
    assert (await service.login()).status_code == 200

    foreign = await service.client.post(
        PATH, headers={**_csrf(service), "Origin": "https://evil.test"}
    )
    assert_problem(foreign, 403, "auth.csrf_failed")
    no_csrf = await service.client.post(PATH, headers={"Origin": ORIGIN})
    assert_problem(no_csrf, 403, "auth.csrf_failed")

    response = await service.client.post(PATH, headers=_csrf(service))
    assert_problem(response, 501, "common.not_implemented")

    bearer = {"Authorization": f"Bearer {service.client.cookies.get(ACCESS_COOKIE)}"}
    service.client.cookies.clear()
    assert_problem(await service.client.post(PATH, headers=bearer), 501, "common.not_implemented")


async def test_realtime_token_rejects_service_token(service: Any) -> None:
    # Сервисный токен (aud = identity) — не пользовательский: публичная ручка его не примет.
    response = await service.client.post(PATH, headers=service.admin_client)
    assert response.status_code == 401
