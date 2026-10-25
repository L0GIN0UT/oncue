"""Двойники хранилища санкций для unit-тестов: мир в памяти с откатом."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from social_service.application.ports.sanctions import SanctionRecord
from social_service.application.ports.sanctions_store import UserCardData

from gv_core.domain import DomainEvent
from gv_testkit import InMemoryIdempotencyStore


@dataclass
class SanctionWorld:
    """«База» санкций между транзакциями."""

    records: dict[UUID, SanctionRecord] = field(default_factory=dict)
    versions: dict[UUID, int] = field(default_factory=dict)
    players: set[UUID] = field(default_factory=set)
    staff_perms: dict[UUID, frozenset[str]] = field(default_factory=dict)
    events: list[DomainEvent] = field(default_factory=list)
    idempotency: InMemoryIdempotencyStore = field(default_factory=InMemoryIdempotencyStore)


class FakeSanctionsRepo:
    def __init__(self, w: SanctionWorld) -> None:
        self.w = w

    async def lock_and_bump(self, user_id: UUID) -> int:
        self.w.versions[user_id] = self.w.versions.get(user_id, 0) + 1
        return self.w.versions[user_id]

    async def get(self, sanction_id: UUID, *, for_update: bool = False) -> SanctionRecord | None:
        return self.w.records.get(sanction_id)

    async def open_of_kind(self, user_id: UUID, kind: str) -> SanctionRecord | None:
        return next(
            (
                r
                for r in self.w.records.values()
                if r.user_id == user_id and r.kind == kind and r.revoked_at is None
            ),
            None,
        )

    async def insert(self, record: SanctionRecord) -> None:
        assert await self.open_of_kind(record.user_id, record.kind) is None, "дубль открытой записи"
        self.w.records[record.id] = record

    async def close(self, sanction_id: UUID, *, at: datetime, by: UUID | None, reason: str) -> bool:
        current = self.w.records[sanction_id]
        if current.revoked_at is not None:
            return False
        self.w.records[sanction_id] = replace(
            current, revoked_at=at, revoked_by=by, revoke_reason=reason
        )
        return True

    async def active(self, user_id: UUID, now: datetime) -> list[SanctionRecord]:
        rows = [
            r
            for r in self.w.records.values()
            if r.user_id == user_id
            and r.revoked_at is None
            and (r.expires_at is None or r.expires_at > now)
        ]
        return sorted(rows, key=lambda r: r.started_at)

    async def history(self, user_id: UUID, limit: int) -> list[SanctionRecord]:
        rows = [r for r in self.w.records.values() if r.user_id == user_id]
        return sorted(rows, key=lambda r: r.started_at, reverse=True)[:limit]

    async def users_with_due(self, now: datetime, limit: int) -> list[UUID]:
        users = {r.user_id for r in self._due(now)}
        return sorted(users)[:limit]

    async def due_of_user(self, user_id: UUID, now: datetime) -> list[SanctionRecord]:
        return [r for r in self._due(now) if r.user_id == user_id]

    async def player_exists(self, user_id: UUID) -> bool:
        return user_id in self.w.players

    async def target_perms(self, user_id: UUID) -> frozenset[str]:
        return self.w.staff_perms.get(user_id, frozenset())

    async def user_card(self, user_id: UUID, case_limit: int) -> UserCardData:
        return UserCardData(nickname=None, counters={}, cases=[])

    def _due(self, now: datetime) -> list[SanctionRecord]:
        return [
            r
            for r in self.w.records.values()
            if r.revoked_at is None and r.expires_at is not None and r.expires_at <= now
        ]


class FakeSanctionsUow:
    """UoW: при выходе без commit мир откатывается к снимку на входе."""

    def __init__(self, w: SanctionWorld) -> None:
        self.w = w
        self._events: list[DomainEvent] = []
        self._snapshot: tuple[object, object] | None = None
        self._committed = False
        self.sanctions = FakeSanctionsRepo(w)
        self.idempotency = w.idempotency

    async def __aenter__(self) -> Self:
        self._snapshot = (copy.copy(self.w.records), copy.copy(self.w.versions))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._committed:
            assert self._snapshot is not None
            self.w.records, self.w.versions = self._snapshot  # type: ignore[assignment]  # снимок
            self.sanctions.w = self.w

    def publish(self, event: DomainEvent) -> None:
        self._events.append(event)

    async def commit(self) -> None:
        self.w.events.extend(self._events)
        self._committed = True
