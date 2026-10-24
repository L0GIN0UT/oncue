"""Порты сезонных событий (5d; ADR 0054-0056)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EventRow:
    """Определение события как хранится."""

    id: UUID
    key: str
    body: dict[str, Any]
    body_sha256: str
    map_key: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunRow:
    """Запуск события как хранится (``status`` - хранимый, эффективный считает домен)."""

    id: UUID
    event_id: UUID
    event_key: str
    seq: int
    status: str
    starts_at: datetime
    ends_at: datetime
    snapshot: dict[str, Any] | None
    body_sha256: str | None
    published_at: datetime | None
    stopped_at: datetime | None
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProgressRow:
    """Прогресс находки игрока за период метрики."""

    find_key: str
    period_key: str
    progress: int
    unlocked_at: datetime | None


@dataclass(frozen=True, slots=True)
class ArchiveRow:
    """Запуск, где у игрока есть прогресс, и сколько находок он открыл."""

    run: RunRow
    found: int
    completed: bool


@dataclass(frozen=True, slots=True)
class FindCount:
    """Сколько игроков открыли находку."""

    find_key: str
    unlocked: int


class EventRepository(Protocol):
    """Определения, запуски и прогресс игроков."""

    # --- определения
    async def add_event(self, row: EventRow) -> bool:
        """Создать событие; ``False`` - ключ занят."""
        ...

    async def get_event(self, key: str, *, for_update: bool = False) -> EventRow | None:
        """Событие по ключу."""
        ...

    async def save_event(self, row: EventRow) -> None:
        """Записать тело, хеш, карту и ``updated_at``."""
        ...

    async def list_events(self) -> list[EventRow]:
        """Все события по ключу."""
        ...

    # --- запуски
    async def lock_publishing(self) -> None:
        """Advisory-lock транзакции: публикации идут по очереди (проверка пересечений)."""
        ...

    async def add_run(self, run: RunRow) -> RunRow:
        """Добавить запуск; ``seq`` назначается репозиторием (следующий в событии)."""
        ...

    async def get_run(
        self, run_id: UUID, *, for_update: bool = False, for_share: bool = False
    ) -> RunRow | None:
        """Запуск по id (``for_share`` - держит строку от остановки до конца транзакции)."""
        ...

    async def save_run(self, run: RunRow) -> None:
        """Записать статус, снимок и метки времени."""
        ...

    async def runs_of(self, event_id: UUID) -> list[RunRow]:
        """Запуски события по ``seq``."""
        ...

    async def latest_runs(self) -> dict[UUID, RunRow]:
        """Последний (по ``seq``) запуск каждого события."""
        ...

    async def has_locking_run(self, event_id: UUID, now: datetime) -> bool:
        """Есть запуск ``published/active``, который ещё не закончился по времени."""
        ...

    async def overlaps(self, starts_at: datetime, ends_at: datetime, *, exclude: UUID) -> bool:
        """Пересекается ли период с опубликованным (не черновиком) запуском другого события.

        Остановленный запуск занимает период до остановки.
        """
        ...

    async def active_at(self, at: datetime) -> RunRow | None:
        """Запуск, в период которого попадает ``at`` (опубликованный, остановка не раньше)."""
        ...

    async def runs_for_key(self, key: str) -> list[RunRow]:
        """Опубликованные запуски события по ключу, новые первыми."""
        ...

    async def to_activate(self, now: datetime) -> list[RunRow]:
        """Опубликованные (хранимо ``published``) запуски, чей срок начался."""
        ...

    async def to_finish(self, now: datetime) -> list[RunRow]:
        """Запуски ``published/active``, чей срок вышел."""
        ...

    async def set_status(self, run_id: UUID, status: str, *, expected: tuple[str, ...]) -> bool:
        """Сменить хранимый статус, если он один из ``expected``; ``False`` - не изменён."""
        ...

    # --- прогресс игроков
    async def progress_of(self, run_id: UUID, user_id: UUID) -> list[ProgressRow]:
        """Строки прогресса игрока по запуску."""
        ...

    async def save_progress(  # noqa: PLR0917 - ключ строки + значения
        self,
        run_id: UUID,
        user_id: UUID,
        find_key: str,
        period_key: str,
        progress: int,
        unlocked_at: datetime | None,
        now: datetime,
    ) -> None:
        """Записать прогресс (``INSERT … ON CONFLICT DO UPDATE``)."""
        ...

    async def add_completion(self, run_id: UUID, user_id: UUID, now: datetime) -> bool:
        """Закрыть событие игроку; ``False`` - уже закрыто."""
        ...

    async def is_completed(self, run_id: UUID, user_id: UUID) -> bool:
        """Закрыто ли событие игроком."""
        ...

    async def archive(self, user_id: UUID, limit: int) -> list[ArchiveRow]:
        """Запуски, где у игрока есть прогресс, новые первыми."""
        ...

    async def stats(self, run_id: UUID) -> tuple[int, int, list[FindCount]]:
        """``(игроков с прогрессом, закрыли событие, открыли по находкам)``."""
        ...
