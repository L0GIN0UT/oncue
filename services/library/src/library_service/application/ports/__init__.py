"""Порты application-слоя library: Unit of Work с репозиториями, лимиты частоты.

Реализации — ``infrastructure`` (PostgreSQL, Redis); use-cases о них не знают.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from gv_core.domain import AggregateRoot
from gv_db.idempotency import IdempotencyStore
from library_service.domain.library import CatalogEntry, ItemStatus, Shelf, ShelfItem, UserLibrary


@dataclass(frozen=True, slots=True)
class ShelfPageRow:
    """Строка страницы полки: единица и её карточка из проекции (``None`` — проекции нет)."""

    item: ShelfItem
    entry: CatalogEntry | None


class LibraryRepository(Protocol):
    """Полки и единицы пользователя."""

    async def lock_user(self, user_id: UUID, now: datetime) -> int:
        """Заблокировать медиатеку пользователя до конца транзакции (строка ``user_versions``,
        создаётся при первой мутации) и вернуть её версию."""
        ...

    async def shelves(self, user_id: UUID) -> list[Shelf]:
        """Все полки пользователя."""
        ...

    async def placements(self, user_id: UUID, media_item_id: UUID) -> list[ShelfItem]:
        """Строки единицы на полках пользователя."""
        ...

    async def shelf_items(self, shelf_id: UUID) -> list[ShelfItem]:
        """Все строки полки (для удаления полки; их не больше ``items_per_shelf_max``)."""
        ...

    async def save(self, library: UserLibrary, now: datetime) -> None:
        """Применить ``library.changes``, счётчики затронутых полок и версию."""
        ...

    async def page(
        self, shelf_id: UUID, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[ShelfPageRow]:
        """Единицы полки: новые сверху, keyset после ``after`` (``added_at``, ``media_item_id``)."""
        ...


class ProjectionRepository(Protocol):
    """Проекция ``catalog.MediaItem*``."""

    async def get(self, media_item_id: UUID, *, for_update: bool = False) -> CatalogEntry | None:
        """Единица из проекции."""
        ...

    async def upsert(self, entry: CatalogEntry, now: datetime) -> None:
        """Записать полный снимок единицы."""
        ...

    async def set_status(
        self, media_item_id: UUID, status: ItemStatus, version: int, now: datetime
    ) -> None:
        """Сменить статус (блокировка не удаляет единицу с полок, D11)."""
        ...


class PurgeRepository(Protocol):
    """Удаление медиатеки пользователя после ``identity.UserDeleted`` (порциями)."""

    async def request(self, user_id: UUID, now: datetime) -> None:
        """Отметить пользователя к удалению (повтор — no-op)."""
        ...

    async def pending(self, limit: int) -> list[UUID]:
        """Пользователи с незавершённым удалением (старые первыми)."""
        ...

    async def delete_batch(self, user_id: UUID, batch: int) -> int:
        """Удалить до ``batch`` строк медиатеки (сначала единицы, затем полки и версию).

        Returns:
            Число удалённых строк; ``0`` — данных пользователя не осталось.
        """
        ...

    async def finish(self, user_id: UUID) -> None:
        """Снять отметку удаления."""
        ...


class LibraryUnitOfWork(Protocol):
    """Транзакция use-case с репозиториями; события агрегатов — в outbox при коммите."""

    library: LibraryRepository
    projection: ProjectionRepository
    purges: PurgeRepository
    idempotency: IdempotencyStore

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отметить агрегат: его события попадут в outbox при коммите."""
        ...

    async def commit(self) -> None:
        """Записать события и закоммитить."""
        ...


UnitOfWorkFactory = Callable[[], LibraryUnitOfWork]


class RateLimiter(Protocol):
    """Лимит частоты (Redis, fail-open с метрикой)."""

    async def hit(self, bucket: str, subject: str, *, limit: int, window_s: int) -> None:
        """Учесть запрос.

        Raises:
            LibraryRateLimitedError: лимит превышен.
        """
        ...


class TitlePolicy(Protocol):
    """Проверка пользовательского текста (``gv_text.ugc.UgcPolicy``)."""

    def check(
        self,
        text: str,
        *,
        max_len: int,
        multiline: bool = False,
        forbid_links: bool = False,
    ) -> str:
        """Нормализованный текст или ``UgcViolation``."""
        ...


__all__: Sequence[str] = (
    "LibraryRepository",
    "LibraryUnitOfWork",
    "ProjectionRepository",
    "PurgeRepository",
    "RateLimiter",
    "ShelfPageRow",
    "TitlePolicy",
    "UnitOfWorkFactory",
)
