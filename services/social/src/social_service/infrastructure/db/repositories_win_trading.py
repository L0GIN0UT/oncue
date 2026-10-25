"""SQL-хранилище детектора win-trading (ADR 0068)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from social_service.application.ports.win_trading import WinTradingStore
from social_service.domain.win_trading import MatchFact, Signal
from social_service.infrastructure.db import tables_win_trading as t


def _fact(row: Any) -> MatchFact:  # Any: строка Core
    return MatchFact(
        match_id=row.match_id,
        user_low=row.user_low,
        user_high=row.user_high,
        winner_id=row.winner_id,
        forfeiter_id=row.forfeiter_id,
        finished_at=row.finished_at,
    )


def _signal(row: Any) -> Signal:  # Any: строка Core
    return Signal(
        key=row.signal_key,
        kind=row.kind,
        user_id=row.user_id,
        other_user_id=row.other_user_id,
        count=row.match_count,
        streak=row.streak,
        severity=row.severity,
        window_s=row.window_s,
        first_at=row.first_at,
        last_at=row.last_at,
        details=row.details,
    )


class SqlWinTradingStore:
    """Факты и сигналы в сессии вызывающего (транзакцию ведёт он)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_fact(self, fact: MatchFact) -> bool:
        m = t.win_trading_matches
        stmt = (
            pg_insert(m)
            .values(
                match_id=fact.match_id,
                user_low=fact.user_low,
                user_high=fact.user_high,
                winner_id=fact.winner_id,
                forfeiter_id=fact.forfeiter_id,
                finished_at=fact.finished_at,
            )
            .on_conflict_do_nothing()
            .returning(m.c.match_id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def pair_facts(self, low: UUID, high: UUID, since: datetime) -> Sequence[MatchFact]:
        m = t.win_trading_matches
        stmt = (
            select(m)
            .where(m.c.user_low == low, m.c.user_high == high, m.c.finished_at >= since)
            .order_by(m.c.finished_at.desc(), m.c.match_id.desc())
        )
        return [_fact(r) for r in (await self._s.execute(stmt)).all()]

    async def forfeit_facts(self, user_id: UUID, since: datetime) -> Sequence[MatchFact]:
        m = t.win_trading_matches
        stmt = (
            select(m)
            .where(m.c.forfeiter_id == user_id, m.c.finished_at >= since)
            .order_by(m.c.finished_at.desc(), m.c.match_id.desc())
        )
        return [_fact(r) for r in (await self._s.execute(stmt)).all()]

    async def sync_signal(self, key: str, signal: Signal | None, at: datetime) -> None:
        s = t.win_trading_signals
        if signal is None:
            await self._s.execute(delete(s).where(s.c.signal_key == key))
            return
        values = {
            "kind": signal.kind,
            "user_id": signal.user_id,
            "other_user_id": signal.other_user_id,
            "match_count": signal.count,
            "streak": signal.streak,
            "severity": signal.severity,
            "window_s": signal.window_s,
            "first_at": signal.first_at,
            "last_at": signal.last_at,
            "details": dict(signal.details),
            "updated_at": at,
        }
        stmt = pg_insert(s).values(signal_key=key, **values)
        await self._s.execute(
            stmt.on_conflict_do_update(index_elements=[s.c.signal_key], set_=values)
        )

    async def signals(
        self,
        *,
        kind: str | None,
        severity: str | None,
        user_id: UUID | None,
        after: tuple[datetime, str] | None,
        limit: int,
    ) -> Sequence[Signal]:
        s = t.win_trading_signals
        stmt = select(s)
        if kind is not None:
            stmt = stmt.where(s.c.kind == kind)
        if severity is not None:
            stmt = stmt.where(s.c.severity == severity)
        if user_id is not None:
            stmt = stmt.where(or_(s.c.user_id == user_id, s.c.other_user_id == user_id))
        if after is not None:
            at, key = after
            stmt = stmt.where(or_(s.c.last_at < at, and_(s.c.last_at == at, s.c.signal_key > key)))
        stmt = stmt.order_by(s.c.last_at.desc(), s.c.signal_key).limit(limit)
        return [_signal(r) for r in (await self._s.execute(stmt)).all()]

    async def delete_user(self, user_id: UUID) -> None:
        m, s = t.win_trading_matches, t.win_trading_signals
        await self._s.execute(
            delete(m).where(or_(m.c.user_low == user_id, m.c.user_high == user_id))
        )
        await self._s.execute(
            delete(s).where(or_(s.c.user_id == user_id, s.c.other_user_id == user_id))
        )


def store_in_session(
    session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[WinTradingStore]]:
    """Хранилище в транзакции потребителя события (коммитит процессор)."""

    @asynccontextmanager
    async def open_store() -> AsyncIterator[WinTradingStore]:
        yield SqlWinTradingStore(session)

    return open_store


def store_with_own_transaction(
    sessions: Callable[[], async_sessionmaker[AsyncSession]],
) -> Callable[[], AbstractAsyncContextManager[WinTradingStore]]:
    """Хранилище в собственной транзакции (ручки админки)."""

    @asynccontextmanager
    async def open_store() -> AsyncIterator[WinTradingStore]:
        async with sessions()() as session, session.begin():
            yield SqlWinTradingStore(session)

    return open_store
