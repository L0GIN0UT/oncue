"""Окно лимита поиска берётся из конфига, а не из константы (ADR 0053)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from social_service.application.friends import FriendsService
from social_service.domain.config import FriendsConfig

from gv_testkit import FakeClock, FakeRandom

pytestmark = pytest.mark.unit


class _Window:
    def __init__(self) -> None:
        self.count = 1
        self.resets_in_s = 1


class SpyLimiter:
    def __init__(self) -> None:
        self.windows: list[int] = []

    async def hit(self, subject: str, window_s: int) -> _Window:
        self.windows.append(window_s)
        return _Window()


async def test_lookup_uses_window_from_config() -> None:
    limiter = SpyLimiter()
    service = FriendsService(
        None, None, FakeClock(), FakeRandom(), limiter=limiter, presence=None, presence_burst=1
    )  # type: ignore[arg-type]
    cfg: Any = type("Cfg", (), {})()
    cfg.friends = FriendsConfig(
        code_attempts_per_hour=30,
        invites_per_minute=10,
        max_friends=3,
        requests_per_day=2,
        lookup_window_s=777,
        invite_window_s=5,
    )
    await service._check_lookup(cfg, UUID(int=1), "1.2.3.4")
    assert limiter.windows == [777, 777]
