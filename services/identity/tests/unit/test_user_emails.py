"""Пакетная выдача подтверждённых адресов (`POST /internal/v1/users/emails`): use-case."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from identity_service.application.queries.user_emails import (
    ListVerifiedEmails,
    UserEmailsLimit,
)
from structlog.testing import capture_logs

from gv_core import DependencyUnavailableError, RateLimitedError

pytestmark = pytest.mark.unit

World = Any  # фейковый мир из conftest
LIMIT = UserEmailsLimit(per_client=3, window_s=60, ids_per_hour=5)
GHOST = UUID("0192f0a0-0000-7000-8000-0000000000ff")


def _uc(world: World, limit: UserEmailsLimit = LIMIT) -> ListVerifiedEmails:
    return ListVerifiedEmails(world.uow, world.strict_limiter, limit)


async def _run(uc: ListVerifiedEmails, ids: list[UUID], client: str = "notifications") -> Any:
    return await uc.execute(client, "jti-1", ids)


async def test_returns_only_verified_active(world: World) -> None:
    ok = await world.make_user("neo_2026", "neo@example.com")
    unverified = await world.make_user("trin_2026", "trin@example.com", verified=False)
    deleted = await world.make_user("mor_2026", "mor@example.com")
    await world.uc.delete_account(deleted, "correct horse battery", ip=None)
    assert await _run(_uc(world), [ok, unverified, deleted, GHOST]) == [(ok, "neo@example.com")]


async def test_duplicates_collapse(world: World) -> None:
    ok = await world.make_user()
    assert await _run(_uc(world), [ok, ok, ok]) == [(ok, "neo@example.com")]


async def test_call_rate_limit_per_client(world: World) -> None:
    ok = await world.make_user()
    uc = _uc(world)
    for _ in range(3):
        await _run(uc, [ok])
    with pytest.raises(RateLimitedError):
        await _run(uc, [ok])
    assert await _run(uc, [ok], "other")  # другой клиент считается отдельно


async def test_ids_window_exhausted_before_sql(world: World) -> None:
    ok = await world.make_user()
    uc = _uc(world, UserEmailsLimit(per_client=100, window_s=60, ids_per_hour=5))
    await _run(uc, [ok, GHOST, UUID(int=1), UUID(int=2)])  # 4 из 5
    with pytest.raises(RateLimitedError) as err:
        await _run(uc, [UUID(int=3), UUID(int=4)])  # 4 + 2 > 5
    assert err.value.details["retry_after"] >= 1


async def test_limiter_unavailable_is_fail_closed(world: World) -> None:
    ok = await world.make_user()
    world.strict_limiter.broken = True
    with pytest.raises(DependencyUnavailableError) as err:
        await _run(_uc(world), [ok])
    assert err.value.details["retry_after"] >= 1


async def test_login_limiter_unaffected_by_strict_failure(world: World) -> None:
    await world.make_user()
    world.strict_limiter.broken = True
    # вход паролем пользуется обычным (fail-open) лимитером и продолжает работать
    issued = await world.uc.login("neo_2026", "correct horse battery", ip="1.2.3.4")
    assert issued is not None


async def test_audit_log_has_counts_jti_and_no_addresses(world: World) -> None:
    ok = await world.make_user()
    with capture_logs() as logs:
        await _run(_uc(world), [ok, GHOST])
    assert "neo@example.com" not in repr(logs)
    event = next(e for e in logs if e["event"] == "users.emails_read")
    assert (event["client_id"], event["token_id"]) == ("notifications", "jti-1")
    assert (event["requested"], event["returned"]) == (2, 1)
