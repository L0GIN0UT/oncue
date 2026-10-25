"""Двойник хранилища win-trading для unit-тестов use-case."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from social_service.domain.win_trading import MatchFact, Signal


class FakeWinTradingStore:
    """Память вместо таблиц: те же правила порядка и выборки, что в SQL-хранилище."""

    def __init__(self) -> None:
        self.facts: dict[UUID, MatchFact] = {}
        self.stored: dict[str, Signal] = {}

    @asynccontextmanager
    async def open(self) -> AsyncIterator[FakeWinTradingStore]:
        yield self

    async def add_fact(self, fact: MatchFact) -> bool:
        if fact.match_id in self.facts:
            return False
        self.facts[fact.match_id] = fact
        return True

    async def pair_facts(self, low: UUID, high: UUID, since: datetime) -> Sequence[MatchFact]:
        found = [
            f
            for f in self.facts.values()
            if (f.user_low, f.user_high) == (low, high) and f.finished_at >= since
        ]
        return sorted(found, key=lambda f: (f.finished_at, f.match_id), reverse=True)

    async def forfeit_facts(self, user_id: UUID, since: datetime) -> Sequence[MatchFact]:
        found = [
            f for f in self.facts.values() if f.forfeiter_id == user_id and f.finished_at >= since
        ]
        return sorted(found, key=lambda f: (f.finished_at, f.match_id), reverse=True)

    async def sync_signal(self, key: str, signal: Signal | None, at: datetime) -> None:
        if signal is None:
            self.stored.pop(key, None)
        else:
            self.stored[key] = signal

    async def signals(
        self,
        *,
        kind: str | None,
        severity: str | None,
        user_id: UUID | None,
        after: tuple[datetime, str] | None,
        limit: int,
    ) -> Sequence[Signal]:
        def order(s: Signal) -> tuple[float, str]:
            return (-s.last_at.timestamp(), s.key)

        items = [
            s
            for s in self.stored.values()
            if (kind is None or s.kind == kind)
            and (severity is None or s.severity == severity)
            and (user_id is None or user_id in (s.user_id, s.other_user_id))
            and (after is None or order(s) > (-after[0].timestamp(), after[1]))
        ]
        items.sort(key=order)
        return items[:limit]

    async def delete_user(self, user_id: UUID) -> None:
        self.facts = {
            k: f for k, f in self.facts.items() if user_id not in (f.user_low, f.user_high)
        }
        self.stored = {
            k: s for k, s in self.stored.items() if user_id not in (s.user_id, s.other_user_id)
        }
