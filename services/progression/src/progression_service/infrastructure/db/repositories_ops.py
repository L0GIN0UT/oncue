"""Postgres: контрольные точки сверки и ретенция (ADR 0062).

Имена таблиц в SQL - только из фиксированных словарей (не из пользовательского ввода).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from progression_service.application.ports import LedgerMismatch
from progression_service.application.ports.ops import Checkpoint
from progression_service.infrastructure.db.tables import reconcile_checkpoints

ZERO_UUID = UUID(int=0)
"""Меньше любого uuid7: «до первой проводки»."""

_CHUNK_SQL = {
    ledger: text(
        f"""
        SELECT id, count(*) OVER () AS n FROM (
            SELECT id FROM {ledger} WHERE id > :after AND id <= :bound ORDER BY id LIMIT :batch
        ) c ORDER BY id DESC LIMIT 1
        """  # noqa: S608 - имя из фиксированного словаря
    )
    for ledger in ("xp_ledger", "currency_ledger")
}

_RANGE_SQL = {
    "xp_ledger": text(
        """
        WITH u AS (SELECT DISTINCT user_id FROM xp_ledger WHERE id > :after AND id <= :upto),
             l AS (SELECT user_id,
                          SUM(CASE direction WHEN 'credit' THEN amount ELSE -amount END) AS s
                     FROM xp_ledger WHERE user_id IN (SELECT user_id FROM u) GROUP BY user_id)
        SELECT 'xp' AS kind, u.user_id AS user_id, CAST(NULL AS text) AS bucket,
               COALESCE(l.s, 0) AS ledger_sum, COALESCE(p.total_xp, 0) AS stored
          FROM u LEFT JOIN l ON l.user_id = u.user_id
                 LEFT JOIN progress p ON p.user_id = u.user_id
         WHERE COALESCE(l.s, 0) <> COALESCE(p.total_xp, 0)
         LIMIT :limit
        """
    ),
    "currency_ledger": text(
        """
        WITH u AS (SELECT DISTINCT user_id FROM currency_ledger WHERE id > :after AND id <= :upto),
             l AS (SELECT user_id, bucket,
                          SUM(CASE direction WHEN 'credit' THEN amount ELSE -amount END) AS s
                     FROM currency_ledger WHERE user_id IN (SELECT user_id FROM u)
                    GROUP BY user_id, bucket),
             b AS (SELECT user_id, bucket, amount FROM balances
                    WHERE user_id IN (SELECT user_id FROM u))
        SELECT 'currency' AS kind, COALESCE(l.user_id, b.user_id) AS user_id,
               COALESCE(l.bucket, b.bucket) AS bucket,
               COALESCE(l.s, 0) AS ledger_sum, COALESCE(b.amount, 0) AS stored
          FROM l FULL OUTER JOIN b ON b.user_id = l.user_id AND b.bucket = l.bucket
         WHERE COALESCE(l.s, 0) <> COALESCE(b.amount, 0)
         LIMIT :limit
        """
    ),
}

_PURGE_MARKS = text(
    """
    DELETE FROM metric_marks WHERE subject_key IN (
        SELECT subject_key FROM metric_marks WHERE at < :cutoff LIMIT :n)
    """
)

# Только дневные/недельные: строка разового задания - ключ идемпотентности его награды.
_PURGE_QUESTS = text(
    """
    DELETE FROM quest_progress WHERE ctid IN (
        SELECT p.ctid FROM quest_progress p
          JOIN quest_assignments a ON a.user_id = p.user_id AND a.quest_key = p.quest_key
                                  AND a.period_start = p.period_start
         WHERE a.period <> 'one_time' AND p.period_start < :cutoff
         LIMIT :n)
    """
)


class SqlLedgerRanges:
    """Запросы инкрементальной сверки по диапазону id (подмешиваются в ``SqlLedger``)."""

    _s: AsyncSession

    async def next_chunk(
        self, ledger: str, after: UUID | None, bound: UUID, batch: int
    ) -> tuple[UUID | None, int]:
        """Конец и размер следующего куска проводок ``after < id <= bound``."""
        row = (
            await self._s.execute(
                _CHUNK_SQL[ledger], {"after": after or ZERO_UUID, "bound": bound, "batch": batch}
            )
        ).first()
        return (None, 0) if row is None else (row.id, int(row.n))

    async def mismatches_in_range(
        self, ledger: str, after: UUID | None, upto: UUID, limit: int
    ) -> list[LedgerMismatch]:
        """Расхождения у игроков с проводками из диапазона."""
        rows = await self._s.execute(
            _RANGE_SQL[ledger], {"after": after or ZERO_UUID, "upto": upto, "limit": limit}
        )
        return [
            LedgerMismatch(
                kind=r.kind,
                user_id=r.user_id,
                bucket=r.bucket,
                ledger_sum=int(r.ledger_sum),
                stored=int(r.stored),
            )
            for r in rows
        ]


class SqlReconcileCheckpoints:
    """Контрольные точки сверки."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def load(self) -> dict[str, Checkpoint]:
        """Точки по имени журнала."""
        rows = await self._s.execute(
            text("SELECT ledger, last_id, last_full_at, full_mismatches FROM reconcile_checkpoints")
        )
        return {
            r.ledger: Checkpoint(r.last_id, r.last_full_at, int(r.full_mismatches)) for r in rows
        }

    async def save(
        self,
        ledger: str,
        *,
        last_id: UUID,
        now: datetime,
        full: bool = False,
        full_mismatches: int = 0,
    ) -> None:
        """Сдвинуть точку (upsert); итог полного прохода - только при ``full``."""
        values: dict[str, object] = {"last_id": last_id, "updated_at": now}
        if full:
            values |= {"last_full_at": now, "full_mismatches": full_mismatches}
        stmt = pg_insert(reconcile_checkpoints).values(ledger=ledger, **values)
        await self._s.execute(
            stmt.on_conflict_do_update(index_elements=[reconcile_checkpoints.c.ledger], set_=values)
        )


class SqlRetention:
    """Удаление устаревших строк пачками."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def purge_metric_marks(self, cutoff: datetime, *, limit: int) -> int:
        """Пачка отметок старше ``cutoff``."""
        result = await self._s.execute(_PURGE_MARKS, {"cutoff": cutoff, "n": limit})
        return int(result.rowcount)  # type: ignore[attr-defined]  # CursorResult у DELETE

    async def purge_quest_progress(self, cutoff: date, *, limit: int) -> int:
        """Пачка прогресса дневных/недельных заданий старше ``cutoff``."""
        result = await self._s.execute(_PURGE_QUESTS, {"cutoff": cutoff, "n": limit})
        return int(result.rowcount)  # type: ignore[attr-defined]  # CursorResult у DELETE
