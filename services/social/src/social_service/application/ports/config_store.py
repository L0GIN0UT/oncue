"""Порт хранилища версий конфига social (ADR 0053)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from gv_db.idempotency import IdempotencyStore


@dataclass(frozen=True, slots=True)
class ConfigVersionRow:
    """Версия конфига."""

    version: int
    status: str  # draft | published | archived
    body: dict[str, Any]
    body_sha256: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


class ConfigStore(Protocol):
    """Версии конфига в одной транзакции."""

    async def lock_writes(self) -> None:
        """Транзакционная блокировка: изменения версий идут по очереди."""
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

    async def published(self) -> ConfigVersionRow | None:
        """Действующая версия."""
        ...

    async def create_draft(
        self, body: dict[str, Any], sha256: str, *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        """Новый черновик со следующим номером."""
        ...

    async def replace_draft(
        self, version: int, body: dict[str, Any], sha256: str, *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        """Заменить тело черновика."""
        ...

    async def publish(self, version: int, *, now: datetime) -> ConfigVersionRow:
        """Архивировать действующую и опубликовать черновик ``version``."""
        ...


class ConfigUnitOfWork(Protocol):
    """Транзакция операций над версиями конфига."""

    @property
    def configs(self) -> ConfigStore:
        """Версии конфига (доступны внутри ``async with``)."""
        ...

    @property
    def idempotency(self) -> IdempotencyStore:
        """Ключи идемпотентности текущей транзакции."""
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Закоммитить."""
        ...


class ConfigUowFactory(Protocol):
    """Фабрика Unit of Work конфига."""

    def __call__(self) -> ConfigUnitOfWork: ...
