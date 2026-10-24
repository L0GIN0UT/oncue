"""Порты хранения санкций (ADR 0052): репозиторий и Unit of Work (поток F3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from gv_core.domain import DomainEvent
from gv_db.idempotency import IdempotencyStore
from social_service.application.ports.sanctions import SanctionRecord


@dataclass(frozen=True, slots=True)
class CaseRow:
    """Кейс игрока для карточки модератора (чтение таблицы ``moderation_cases``)."""

    id: UUID
    target_kind: str
    target_id: UUID
    target_user_id: UUID
    status: str
    reports_count: int
    assignee_id: UUID | None
    opened_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class UserCardData:
    """Данные карточки игрока: ник, счётчики порядочности, кейсы."""

    nickname: str | None
    counters: dict[str, int]
    cases: list[CaseRow]


class SanctionsRepository(Protocol):
    """Санкции игроков и версия их набора."""

    async def lock_and_bump(self, user_id: UUID) -> int:
        """Увеличить ``version`` игрока и удержать блокировку до конца транзакции.

        Сериализует конкурентные изменения набора санкций одного игрока; возвращает новую
        версию (1 при первом изменении).
        """
        ...

    async def get(self, sanction_id: UUID, *, for_update: bool = False) -> SanctionRecord | None:
        """Запись по идентификатору."""
        ...

    async def open_of_kind(self, user_id: UUID, kind: str) -> SanctionRecord | None:
        """Не снятая запись вида (срок может быть уже вышедшим, но задача его не закрыла)."""
        ...

    async def insert(self, record: SanctionRecord) -> None:
        """Сохранить новую запись."""
        ...

    async def close(self, sanction_id: UUID, *, at: datetime, by: UUID | None, reason: str) -> bool:
        """Закрыть запись; ``False`` - уже закрыта."""
        ...

    async def active(self, user_id: UUID, now: datetime) -> list[SanctionRecord]:
        """Действующие: не сняты и срок не вышел; по возрастанию ``started_at``."""
        ...

    async def history(self, user_id: UUID, limit: int) -> list[SanctionRecord]:
        """История игрока, новые первыми."""
        ...

    async def users_with_due(self, now: datetime, limit: int) -> list[UUID]:
        """Игроки, у которых есть не снятая запись с вышедшим сроком."""
        ...

    async def due_of_user(self, user_id: UUID, now: datetime) -> list[SanctionRecord]:
        """Не снятые записи игрока с вышедшим сроком."""
        ...

    async def player_exists(self, user_id: UUID) -> bool:
        """Известен ли игрок (проекция пользователей)."""
        ...

    async def user_card(self, user_id: UUID, case_limit: int) -> UserCardData:
        """Ник, счётчики порядочности и последние кейсы игрока (простое чтение)."""
        ...


class SanctionsUnitOfWork(Protocol):
    """Транзакция use-case санкций; события уходят в outbox при коммите."""

    @property
    def sanctions(self) -> SanctionsRepository:
        """Репозиторий санкций в транзакции."""
        ...

    @property
    def idempotency(self) -> IdempotencyStore:
        """Ключи идемпотентности в транзакции."""
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def publish(self, event: DomainEvent) -> None:
        """Событие уйдёт в outbox при коммите."""
        ...

    async def commit(self) -> None:
        """Зафиксировать."""
        ...


type SanctionsUowFactory = Callable[[], SanctionsUnitOfWork]
