"""Порты admin-API progression: версии конфига, аудит, журнал для админки, ключи идемпотентности."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from gv_db.idempotency import IdempotencyStore
from progression_service.application.ports import ProgressionUnitOfWork
from progression_service.domain.ledger import Entry

type Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConfigVersionRow:
    """Версия конфига как хранится (с телом)."""

    version: int
    status: str
    body: dict[str, Any]
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminAction:
    """Запись аудита."""

    id: UUID
    actor_id: UUID
    action: str
    target: str
    reason: str
    at: datetime
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """Проводка Версов вместе с id строки (для курсора)."""

    id: UUID
    entry: Entry


class ConfigAdminRepository(Protocol):
    """Версии конфига для админки. Все изменения — после ``lock_writes``."""

    async def lock_writes(self) -> None:
        """Сериализовать изменения версий (номер новой версии, черновик, публикация)."""
        ...

    async def list(self) -> list[ConfigVersionRow]:
        """Все версии, новые первыми."""
        ...

    async def get(self, version: int) -> ConfigVersionRow | None:
        """Версия по номеру."""
        ...

    async def draft(self) -> ConfigVersionRow | None:
        """Текущий черновик."""
        ...

    async def create_draft(
        self, body: dict[str, Any], *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        """Новый черновик со следующим номером."""
        ...

    async def replace_draft(
        self, version: int, body: dict[str, Any], *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        """Заменить тело существующего черновика (обновляет автора и ``created_at``)."""
        ...

    async def publish(self, version: int, *, now: datetime) -> ConfigVersionRow:
        """Прежнюю действующую - в архив, ``version`` - действующая (одна транзакция)."""
        ...


class AuditLog(Protocol):
    """Аудит ручных действий."""

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        ...


class AdminLedgerRepository(Protocol):
    """Чтение журнала Версов для админки."""

    async def granted_between(self, user_id: UUID, start: datetime, end: datetime) -> int:
        """Сумма ручных выдач игроку в ``[start, end)`` (сторнированные тоже считаются)."""
        ...

    async def page(
        self, user_id: UUID, *, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[LedgerRow]:
        """Проводки игрока, новые первыми, строго до курсора ``(at, id)``."""
        ...


class StatsSource(Protocol):
    """Счётчики для карточек заданий и бейджей.

    Таблиц заданий и бейджей в потоке P3 нет; поток P2 при слиянии заменяет реализацию.
    """

    async def quest_completions(self) -> dict[str, int] | None:
        """Выполнений по ключу задания."""
        ...

    async def badge_unlocks(self) -> dict[str, int] | None:
        """Выдано по ключу бейджа."""
        ...


class AdminUnitOfWork(ProgressionUnitOfWork, Protocol):
    """Транзакция admin-use-case: ядро progression плюс репозитории админки."""

    @property
    def config_admin(self) -> ConfigAdminRepository: ...

    @property
    def audit(self) -> AuditLog: ...

    @property
    def admin_ledger(self) -> AdminLedgerRepository: ...

    @property
    def stats(self) -> StatsSource: ...

    @property
    def idempotency(self) -> IdempotencyStore: ...


AdminUowFactory = Callable[[], AdminUnitOfWork]
