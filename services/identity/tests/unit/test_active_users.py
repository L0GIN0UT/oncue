"""Постраничный список активных пользователей (`GET /internal/v1/users/active`): use-case."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from identity_service.application.queries.active_users import ActiveUsersLimit, ListActiveUsers
from structlog.testing import capture_logs

from gv_core import DependencyUnavailableError, RateLimitedError

pytestmark = pytest.mark.unit

World = Any  # фейковый мир из conftest
LIMIT = ActiveUsersLimit(max_page=2, per_client=3, window_s=60)


def _uc(world: World, limit: ActiveUsersLimit = LIMIT) -> ListActiveUsers:
    return ListActiveUsers(world.uow, world.strict_limiter, limit)


async def _make(world: World, n: int) -> list[UUID]:
    return [await world.make_user(f"user_{i}_2026", f"u{i}@example.com") for i in range(n)]


async def test_pages_by_cursor_in_id_order_without_gaps(world: World) -> None:
    ids = sorted(await _make(world, 5))
    uc = _uc(world)
    first = await uc.execute("notifications", "jti", after=None, limit=2)
    second = await uc.execute("notifications", "jti", after=first.next_cursor, limit=2)
    assert first.ids == ids[:2] and first.next_cursor == ids[1]
    assert second.ids == ids[2:4] and second.next_cursor == ids[3]


async def test_last_page_has_no_cursor(world: World) -> None:
    ids = sorted(await _make(world, 3))
    page = await _uc(world).execute("notifications", "jti", after=ids[1], limit=2)
    assert page.ids == ids[2:] and page.next_cursor is None


async def test_limit_is_capped_by_setting(world: World) -> None:
    await _make(world, 4)
    page = await _uc(world).execute("notifications", "jti", after=None, limit=100)
    assert len(page.ids) == LIMIT.max_page


async def test_deleted_users_are_not_listed(world: World) -> None:
    ok, gone = await _make(world, 2)
    await world.uc.delete_account(gone, "correct horse battery", ip=None)
    page = await _uc(world).execute("notifications", "jti", after=None, limit=2)
    assert page.ids == [ok]


async def test_call_rate_limit_per_client(world: World) -> None:
    await _make(world, 1)
    uc = _uc(world)
    for _ in range(3):
        await uc.execute("notifications", "jti", after=None, limit=1)
    with pytest.raises(RateLimitedError):
        await uc.execute("notifications", "jti", after=None, limit=1)
    assert await uc.execute("other", "jti", after=None, limit=1)


async def test_limiter_unavailable_is_fail_closed(world: World) -> None:
    world.strict_limiter.broken = True
    with pytest.raises(DependencyUnavailableError):
        await _uc(world).execute("notifications", "jti", after=None, limit=1)


async def test_audit_log_has_counts_and_no_emails(world: World) -> None:
    await _make(world, 1)
    with capture_logs() as logs:
        await _uc(world).execute("notifications", "jti-7", after=None, limit=2)
    assert "@example.com" not in repr(logs)
    event = next(e for e in logs if e["event"] == "users.active_listed")
    assert (event["client_id"], event["token_id"], event["returned"]) == (
        "notifications",
        "jti-7",
        1,
    )
