"""GET /internal/v1/users/active: доступ по scope, постраничный курсор, только id, лимиты."""

from __future__ import annotations

from typing import Any

import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_problem

from .conftest import GAME_SECRET, NOTIFICATIONS_SECRET, service_token_headers

pytestmark = pytest.mark.component

URL = "/internal/v1/users/active"


async def _notifier(client: Any) -> dict[str, str]:
    return await service_token_headers(client, "notifications", NOTIFICATIONS_SECRET)


async def _register_many(service: Any, n: int) -> None:
    for i in range(n):
        await service.register(f"user_{i}_2026", f"u{i}@example.com")
        service.forget_session()


async def test_pages_through_all_active_users_with_ids_only(service: Any) -> None:
    await _register_many(service, 3)
    headers = await _notifier(service.client)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["after"] = cursor
        response = await service.client.get(URL, params=params, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert set(body) == {"items", "next_cursor"}
        seen += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3 and seen == sorted(seen)
    assert "@" not in repr(seen)


async def test_default_limit_comes_from_settings(seeding: Any) -> None:
    settings, prefix = seeding.settings(users_active={"max_page": 1})
    async with seeding.app(settings, prefix) as (client, _admin):
        headers = await _notifier(client)
        response = await client.get(URL, params={"limit": 50}, headers=headers)
        assert response.status_code == 200
        assert len(response.json()["items"]) <= 1


async def test_no_token_is_401(service: Any) -> None:
    assert_problem(await service.client.get(URL), 401, "auth.unauthenticated")


async def test_emails_scope_alone_is_not_enough(service: Any) -> None:
    response = await service.client.get(URL, headers=service.admin_client)
    assert_problem(response, 403, "auth.forbidden")


async def test_foreign_audience_token_is_rejected(service: Any) -> None:
    headers = await service_token_headers(service.client, "game", GAME_SECRET, "media_pipeline")
    assert_problem(await service.client.get(URL, headers=headers), 401, "auth.token_invalid")


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": "x"}, {"after": "not-a-uuid"}])
async def test_query_is_validated(service: Any, params: dict[str, Any]) -> None:
    response = await service.client.get(URL, params=params, headers=await _notifier(service.client))
    assert response.status_code == 422


async def test_call_rate_limit_per_client(seeding: Any) -> None:
    settings, prefix = seeding.settings(users_active={"per_client": 2, "window_s": 60})
    async with seeding.app(settings, prefix) as (client, _admin):
        headers = await _notifier(client)
        for _ in range(2):
            assert (await client.get(URL, headers=headers)).status_code == 200
        assert_problem(await client.get(URL, headers=headers), 429, "rate.limited")


async def test_after_cursor_accepts_uuid(service: Any) -> None:
    response = await service.client.get(
        URL, params={"after": str(uuid7())}, headers=await _notifier(service.client)
    )
    assert response.status_code == 200
