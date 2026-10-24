"""Двойники портов social для unit-тестов: мир в памяти с откатом и конфиг из примера контракта."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from social_service.application.ports.config import ConfigUnavailableError
from social_service.application.ports.discussion import NewReport
from social_service.domain.config import SocialConfig
from social_service.domain.discussion import CollectionRef, Comment

from gv_core.domain import DomainEvent
from gv_testkit import FakeClock, InMemoryIdempotencyStore

_EXAMPLE = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "social"
    / "examples"
    / "social_config.v1.json"
)


def example_config(**overrides: dict[str, Any]) -> SocialConfig:
    """Конфиг из примера контракта; ``overrides`` заменяют значения секций."""
    body: dict[str, Any] = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    for section, values in overrides.items():
        body[section] = {**body[section], **values}
    return SocialConfig.from_body(1, body)


class FakeConfig:
    """``ConfigProvider`` с заменяемой версией; ``None`` - версии нет."""

    def __init__(self, config: SocialConfig | None = None) -> None:
        self.config = config if config is not None else example_config()
        self.available = True

    async def current(self) -> SocialConfig:
        if not self.available:
            raise ConfigUnavailableError("нет версии")
        return self.config


class FakeLimiter:
    """Счётчик попыток в памяти (окно не моделируется)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)

    async def hit(self, subject: str, *, window_s: int) -> int:
        self.counts[subject] += 1
        return self.counts[subject]


@dataclass
class World:
    """Состояние «базы» между транзакциями."""

    comments: dict[UUID, Comment] = field(default_factory=dict)
    collections: dict[UUID, CollectionRef] = field(default_factory=dict)
    nicknames: dict[UUID, str] = field(default_factory=dict)
    reports: list[NewReport] = field(default_factory=list)
    cases: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    case_reports: set[tuple[UUID, UUID]] = field(default_factory=set)
    participants: dict[tuple[UUID, UUID], bool] = field(default_factory=dict)
    counters: dict[UUID, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    events: list[DomainEvent] = field(default_factory=list)


class _Comments:
    def __init__(self, w: World) -> None:
        self.w = w

    async def add(self, comment: Comment) -> None:
        self.w.comments[comment.id] = comment

    async def get(self, comment_id: UUID) -> Comment | None:
        return self.w.comments.get(comment_id)

    async def set_body(self, comment_id: UUID, body: str, edited_at: datetime) -> None:
        self.w.comments[comment_id] = replace(
            self.w.comments[comment_id], body=body, edited_at=edited_at
        )

    async def mark_deleted(
        self, comment_id: UUID, *, at: datetime, by: UUID | None, role: str
    ) -> bool:
        current = self.w.comments[comment_id]
        if current.is_deleted:
            return False
        self.w.comments[comment_id] = replace(
            current, body="", deleted_at=at, deleted_by=by, deleted_role=role
        )
        return True

    async def page(
        self, collection_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Comment]:
        rows = [
            c
            for c in self.w.comments.values()
            if c.collection_id == collection_id and not c.is_deleted
        ]
        rows.sort(key=lambda c: (c.created_at, c.id), reverse=True)
        if after is not None:
            rows = [c for c in rows if (c.created_at, c.id) < after]
        return rows[:limit]

    async def count(self, collection_id: UUID) -> int:
        return len(await self.page(collection_id, after=None, limit=10**6))

    async def anonymize_author(self, user_id: UUID, *, at: datetime) -> list[Comment]:
        live = [c for c in self.w.comments.values() if c.author_id == user_id and not c.is_deleted]
        for c in live:
            await self.mark_deleted(c.id, at=at, by=None, role="account_deleted")
        return live


class _Collections:
    def __init__(self, w: World) -> None:
        self.w = w

    async def get(self, collection_id: UUID) -> CollectionRef | None:
        return self.w.collections.get(collection_id)

    async def apply(self, ref: CollectionRef) -> None:
        known = self.w.collections.get(ref.id)
        if known is None or known.version < ref.version:
            self.w.collections[ref.id] = ref


class _Nicknames:
    def __init__(self, w: World) -> None:
        self.w = w

    async def get(self, user_ids: list[UUID]) -> dict[UUID, str]:
        return {u: self.w.nicknames[u] for u in user_ids if u in self.w.nicknames}


class _Reports:
    def __init__(self, w: World) -> None:
        self.w = w

    async def lock_target(self, kind: str, target_id: UUID) -> None:
        return None

    async def insert(self, report: NewReport) -> UUID | None:
        key = (report.author_id, report.target_kind, report.target_id)
        if any((r.author_id, r.target_kind, r.target_id) == key for r in self.w.reports):
            return None
        self.w.reports.append(report)
        return report.id

    async def existing_id(self, author_id: UUID, kind: str, target_id: UUID) -> UUID:
        return next(
            r.id
            for r in self.w.reports
            if (r.author_id, r.target_kind, r.target_id) == (author_id, kind, target_id)
        )

    async def count_by_author_since(self, author_id: UUID, since: datetime) -> int:
        return sum(1 for r in self.w.reports if r.author_id == author_id and r.created_at >= since)

    async def recent_ids(self, kind: str, target_id: UUID, since: datetime) -> list[UUID]:
        return [
            r.id
            for r in self.w.reports
            if r.target_kind == kind and r.target_id == target_id and r.created_at >= since
        ]

    async def open_case(
        self, case_id: UUID, kind: str, target_id: UUID, target_user_id: UUID, now: datetime
    ) -> UUID | None:
        if await self.active_case_id(kind, target_id) is not None:
            return None
        self.w.cases[case_id] = {"kind": kind, "target": target_id, "status": "open"}
        return case_id

    async def active_case_id(self, kind: str, target_id: UUID) -> UUID | None:
        return next(
            (
                cid
                for cid, c in self.w.cases.items()
                if c["kind"] == kind and c["target"] == target_id and c["status"] != "resolved"
            ),
            None,
        )

    async def attach(self, case_id: UUID, report_ids: list[UUID]) -> None:
        self.w.case_reports |= {(case_id, rid) for rid in report_ids}

    async def anonymize_author(self, user_id: UUID) -> int:
        n = 0
        for i, r in enumerate(self.w.reports):
            if r.author_id == user_id and r.text is not None:
                self.w.reports[i] = replace(r, text=None)
                n += 1
        return n


class _Matches:
    def __init__(self, w: World) -> None:
        self.w = w

    async def add_participants(
        self, match_id: UUID, user_ids: list[UUID], *, solo: bool, finished_at: datetime
    ) -> list[UUID]:
        fresh = [u for u in user_ids if (match_id, u) not in self.w.participants]
        for u in fresh:
            self.w.participants[match_id, u] = solo
        return fresh

    async def shared_match(self, match_id: UUID, first: UUID, second: UUID) -> bool:
        a, b = (
            self.w.participants.get((match_id, first)),
            self.w.participants.get((match_id, second)),
        )
        return a is False and b is False

    async def bump(self, user_id: UUID, field_name: str, by: int = 1) -> None:
        row = self.w.counters[user_id]
        row[field_name] = row.get(field_name, 0) + by


class FakeUow:
    """UoW над ``World``: без коммита изменения откатываются."""

    def __init__(self, world: World, idempotency: InMemoryIdempotencyStore) -> None:
        self.world = world
        self.comments = _Comments(world)
        self.collections = _Collections(world)
        self.nicknames = _Nicknames(world)
        self.reports = _Reports(world)
        self.matches = _Matches(world)
        self.idempotency = idempotency
        self._pending: list[DomainEvent] = []
        self._snapshot: dict[str, Any] | None = None
        self._committed = False

    async def __aenter__(self) -> Self:
        self._snapshot = copy.deepcopy(self.world.__dict__)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._committed and self._snapshot is not None:
            self.world.__dict__.update(self._snapshot)

    def emit(self, event: DomainEvent) -> None:
        self._pending.append(event)

    async def commit(self) -> None:
        self.world.events.extend(self._pending)
        self._pending = []
        self._committed = True


@dataclass
class Harness:
    """Мир, часы и фабрика UoW для unit-тестов use-case."""

    world: World = field(default_factory=World)
    clock: FakeClock = field(default_factory=FakeClock)
    config: FakeConfig = field(default_factory=FakeConfig)
    limiter: FakeLimiter = field(default_factory=FakeLimiter)

    def __post_init__(self) -> None:
        self._idem = InMemoryIdempotencyStore(self.clock)

    def uow(self) -> FakeUow:
        return FakeUow(self.world, self._idem)

    def events(self, name: str) -> list[DomainEvent]:
        return [e for e in self.world.events if type(e).__name__ == name]
