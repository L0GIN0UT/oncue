"""Кэш выключателей типов: короткий TTL, сброс при изменении."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from notifications_service.application.type_switch import TypeSwitch
from notifications_service.domain.notification import TypeFlag

from gv_core.ids import uuid7
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Flags:
    def __init__(self) -> None:
        self.data: dict[str, TypeFlag] = {}
        self.reads = 0

    async def all(self) -> dict[str, TypeFlag]:
        self.reads += 1
        return dict(self.data)


class _Uow:
    def __init__(self, flags: _Flags) -> None:
        self.type_flags = flags


def _off(key: str) -> TypeFlag:
    return TypeFlag(type_key=key, enabled=False, changed_by=uuid7(), changed_at=NOW)


async def test_type_without_record_is_enabled_by_default() -> None:
    flags = _Flags()
    switch = TypeSwitch(FakeClock(), ttl_s=10)
    assert await switch.is_enabled(_Uow(flags), "level_up") is True  # type: ignore[arg-type]


async def test_disabled_type_is_reported_and_cache_serves_within_ttl() -> None:
    clock, flags = FakeClock(), _Flags()
    flags.data["level_up"] = _off("level_up")
    switch = TypeSwitch(clock, ttl_s=10)
    uow = _Uow(flags)

    assert await switch.is_enabled(uow, "level_up") is False  # type: ignore[arg-type]
    assert await switch.is_enabled(uow, "badge_unlocked") is True  # type: ignore[arg-type]
    assert flags.reads == 1

    flags.data.clear()  # в БД включили, но другой процесс увидит это только после TTL
    clock.advance(seconds=9)
    assert await switch.is_enabled(uow, "level_up") is False  # type: ignore[arg-type]
    clock.advance(seconds=2)
    assert await switch.is_enabled(uow, "level_up") is True  # type: ignore[arg-type]
    assert flags.reads == 2


async def test_invalidate_forces_reload() -> None:
    flags = _Flags()
    switch = TypeSwitch(FakeClock(), ttl_s=10)
    uow = _Uow(flags)
    assert await switch.is_enabled(uow, "level_up") is True  # type: ignore[arg-type]
    flags.data["level_up"] = _off("level_up")
    switch.invalidate()
    assert await switch.is_enabled(uow, "level_up") is False  # type: ignore[arg-type]


async def test_zero_ttl_disables_cache() -> None:
    flags = _Flags()
    switch = TypeSwitch(FakeClock(), ttl_s=0)
    uow = _Uow(flags)
    await switch.is_enabled(uow, "a")  # type: ignore[arg-type]
    await switch.is_enabled(uow, "a")  # type: ignore[arg-type]
    assert flags.reads == 2
