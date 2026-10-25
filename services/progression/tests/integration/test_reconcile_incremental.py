"""Инкрементальная сверка журнала (ADR 0043, 0062): контрольная точка и полный проход по сроку."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.ports import ProgressionUnitOfWork
from progression_service.application.reconcile import IncrementalReconciler, uuid7_floor
from sqlalchemy import text

from gv_core.ids import uuid7

pytestmark = pytest.mark.integration

Service = Any  # фикстура `progression` описана в conftest.py
FULL_EVERY = timedelta(hours=24)
LAG = timedelta(seconds=1)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class _Ids:
    """«Часы идентификаторов» сверки: ``pin`` фиксирует границу покрытия на текущий момент."""

    def __init__(self) -> None:
        self.pinned: int | None = None

    async def pin(self) -> None:
        await asyncio.sleep(0.02)  # проводки, созданные до вызова, строго старше границы
        self.pinned = _now_ms()

    def now_ms(self, lag: timedelta) -> int:
        # Граница = now_ms - lag; при закреплении она ровно ``pinned``.
        if self.pinned is None:
            return _now_ms()
        return self.pinned + int(lag.total_seconds() * 1000)


def _reconciler(
    progression: Service,
    ids: _Ids,
    *,
    lag: timedelta = LAG,
    batch: int = 1000,
    factory: Callable[[], ProgressionUnitOfWork] | None = None,
) -> IncrementalReconciler:
    return IncrementalReconciler(
        factory or progression.container.uow_factory(),
        progression.clock,
        limit=100,
        statement_timeout_ms=5000,
        full_every=FULL_EVERY,
        lag=lag,
        batch=batch,
        now_ms=lambda: ids.now_ms(lag),
    )


async def _exec(progression: Service, sql: str, **params: Any) -> None:
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(text(sql), params)


async def _corrupt_balance(progression: Service, user: UUID, delta: int) -> None:
    await _exec(
        progression, "UPDATE balances SET amount = amount + :d WHERE user_id = :u", u=user, d=delta
    )


async def _player(progression: Service) -> UUID:
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100]))
    return user


async def _checkpoint(progression: Service, ledger: str) -> UUID | None:
    async with progression.container.uow_factory()() as uow:
        state = await uow.reconcile.load()
    return state[ledger].last_id if ledger in state else None


def test_uuid7_floor_orders_like_generated_ids() -> None:
    ms = _now_ms()
    assert uuid7(ms - 1) < uuid7_floor(ms) <= uuid7(ms)
    assert uuid7_floor(ms).version == 7


async def test_first_run_is_full_and_finds_corruption_in_old_rows(progression: Service) -> None:
    await progression.publish_config()
    user = await _player(progression)
    await _corrupt_balance(progression, user, 5)

    result = await _reconciler(progression, _Ids()).run()

    assert result.full is True
    assert [(m.kind, m.stored - m.ledger_sum) for m in result.mismatches] == [("currency", 5)]
    assert await _checkpoint(progression, "xp_ledger") is not None
    assert await _checkpoint(progression, "currency_ledger") is not None


async def test_next_run_checks_only_players_with_new_entries(progression: Service) -> None:
    await progression.publish_config()
    ids = _Ids()
    old = await _player(progression)
    await ids.pin()
    reconciler = _reconciler(progression, ids)
    assert (await reconciler.run()).mismatches == []

    # Правка в обход журнала у игрока без новых проводок: инкрементальный проход её не видит…
    await _corrupt_balance(progression, old, 3)
    second = await reconciler.run()
    assert second.full is False
    assert second.mismatches == []

    # …а у игрока с новой проводкой расхождение находится.
    fresh = await _player(progression)
    await _corrupt_balance(progression, fresh, 4)
    await ids.pin()
    third = await reconciler.run()
    assert third.full is False
    assert [(m.user_id, m.stored - m.ledger_sum) for m in third.mismatches] == [(fresh, 4)]


async def test_full_pass_after_interval_catches_bypass_edit(progression: Service) -> None:
    await progression.publish_config()
    ids = _Ids()
    user = await _player(progression)
    await ids.pin()
    reconciler = _reconciler(progression, ids)
    await reconciler.run()
    await _exec(
        progression, "UPDATE progress SET total_xp = total_xp + 9 WHERE user_id = :u", u=user
    )
    assert (await reconciler.run()).mismatches == []

    progression.clock.advance(FULL_EVERY + timedelta(seconds=1))
    result = await reconciler.run()

    assert result.full is True
    assert [(m.kind, m.stored - m.ledger_sum) for m in result.mismatches] == [("xp", 9)]
    # Следующий (инкрементальный) проход помнит открытое расхождение для метрики.
    again = await reconciler.run()
    assert again.full is False
    assert again.outstanding == 1


async def test_entries_inside_lag_are_left_for_the_next_run(progression: Service) -> None:
    """Проводка моложе лага не покрывается проходом: транзакция с меньшим id могла не успеть."""
    await progression.publish_config()
    ids = _Ids()
    await _player(progression)
    await ids.pin()
    await _reconciler(progression, ids).run()
    before = await _checkpoint(progression, "currency_ledger")

    user = await _player(progression)
    await _corrupt_balance(progression, user, 2)
    # Граница «сейчас - час» левее точки: проводка нового игрока ещё «слишком свежая».
    young = _reconciler(progression, _Ids(), lag=timedelta(hours=1))
    assert (await young.run()).mismatches == []
    assert await _checkpoint(progression, "currency_ledger") == before  # точка не откатилась

    await ids.pin()
    caught = await _reconciler(progression, ids).run()
    assert [(m.user_id, m.stored - m.ledger_sum) for m in caught.mismatches] == [(user, 2)]


async def test_chunks_cover_every_player_and_advance_checkpoint(progression: Service) -> None:
    await progression.publish_config()
    ids = _Ids()
    await ids.pin()
    reconciler = _reconciler(progression, ids, batch=2)
    await reconciler.run()
    users = [await _player(progression) for _ in range(5)]
    for user in users:
        await _corrupt_balance(progression, user, 1)
    await ids.pin()

    result = await reconciler.run()

    assert {m.user_id for m in result.mismatches} == set(users)


class _Reconcile:
    """Репозиторий точек, у которого запись падает, пока ``armed`` (сбой посреди прохода)."""

    def __init__(self, real: Any, owner: _FailingCheckpoint) -> None:
        self._real = real
        self._owner = owner

    async def load(self) -> Any:
        return await self._real.load()

    async def save(self, *args: Any, **kwargs: Any) -> None:
        if self._owner.armed:
            raise RuntimeError("сбой записи точки")
        await self._real.save(*args, **kwargs)


class _Proxy:
    def __init__(self, uow: Any, owner: _FailingCheckpoint) -> None:
        self._uow = uow
        self._owner = owner

    @property
    def reconcile(self) -> _Reconcile:
        return _Reconcile(self._uow.reconcile, self._owner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._uow, name)

    async def __aenter__(self) -> _Proxy:
        await self._uow.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._uow.__aexit__(*exc)


class _FailingCheckpoint:
    def __init__(self, factory: Callable[[], ProgressionUnitOfWork]) -> None:
        self._factory = factory
        self.armed = True

    def __call__(self) -> Any:
        return _Proxy(self._factory(), self)


async def test_failure_before_checkpoint_save_loses_nobody(progression: Service) -> None:
    await progression.publish_config()
    user = await _player(progression)
    await _corrupt_balance(progression, user, 5)
    failing = _FailingCheckpoint(progression.container.uow_factory())
    broken = _reconciler(progression, _Ids(), factory=failing)

    with pytest.raises(RuntimeError, match="сбой записи"):
        await broken.run()
    assert await _checkpoint(progression, "xp_ledger") is None  # точка не сдвинулась

    failing.armed = False
    result = await broken.run()
    assert [(m.user_id, m.stored - m.ledger_sum) for m in result.mismatches] == [(user, 5)]
