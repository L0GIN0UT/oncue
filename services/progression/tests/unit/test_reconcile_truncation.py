"""Инкрементальная сверка: усечённая выдача расхождений не должна терять игроков (ADR 0062)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.ports import LedgerMismatch
from progression_service.application.ports.ops import Checkpoint
from progression_service.application.reconcile import LEDGERS, IncrementalReconciler

from gv_core.ids import uuid7
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Db:
    """Журнал в памяти: проводка = (id, игрок); расхождение у каждого игрока."""

    def __init__(self, entries: list[tuple[UUID, UUID]]) -> None:
        self.entries = sorted(entries)
        self.points: dict[str, Checkpoint] = {
            name: Checkpoint(UUID(int=0), NOW, 0) for name in LEDGERS
        }
        self.saved: list[tuple[str, UUID]] = []

    def _in(self, after: UUID | None, upto: UUID) -> list[tuple[UUID, UUID]]:
        return [e for e in self.entries if (after or UUID(int=0)) < e[0] <= upto]


class _Ledger:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def next_chunk(
        self, ledger: str, after: UUID | None, bound: UUID, batch: int
    ) -> tuple[UUID | None, int]:
        rows = self._db._in(after, bound)[:batch]
        return (None, 0) if not rows else (rows[-1][0], len(rows))

    async def mismatches_in_range(
        self, ledger: str, after: UUID | None, upto: UUID, limit: int
    ) -> list[LedgerMismatch]:
        users = list(dict.fromkeys(u for _, u in self._db._in(after, upto)))
        return [LedgerMismatch("xp", u, None, 0, 1) for u in users[:limit]]


class _Reconcile:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def load(self) -> dict[str, Checkpoint]:
        return dict(self._db.points)

    async def save(self, ledger: str, *, last_id: UUID, now: datetime, **_: Any) -> None:
        self._db.points[ledger] = Checkpoint(last_id, NOW, 0)
        self._db.saved.append((ledger, last_id))


class _Uow:
    def __init__(self, db: _Db) -> None:
        self.ledger = _Ledger(db)
        self.reconcile = _Reconcile(db)

    async def __aenter__(self) -> _Uow:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def read_only(self, *, statement_timeout_ms: int) -> None:
        return None

    async def commit(self) -> None:
        return None


async def test_more_mismatches_than_limit_are_all_found_and_point_does_not_skip() -> None:
    users = [uuid7() for _ in range(7)]
    db = _Db([(uuid7(), u) for u in users])
    bound_ms = 2**47  # граница заведомо правее всех id
    reconciler = IncrementalReconciler(
        lambda: _Uow(db),  # type: ignore[arg-type,return-value]  # двойник UoW
        FakeClock(NOW),
        limit=2,
        statement_timeout_ms=1000,
        full_every=timedelta(days=1),
        lag=timedelta(0),
        batch=100,
        now_ms=lambda: bound_ms,
    )

    result = await reconciler.run()

    assert {m.user_id for m in result.mismatches} == set(users)
    # Точка двигалась кусками, а не перескочила весь диапазон одним шагом.
    assert len(db.saved) > len(LEDGERS)
