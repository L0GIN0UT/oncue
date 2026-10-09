"""Порты: интерфейсы репозиториев и шлюзов, реализуются в infrastructure."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from catalog_service.application.dto import (
    AdminItemFilter,
    ClueStatus,
    ImportedItem,
    ItemSummary,
    RecordedAction,
    SearchDocument,
    SearchQuery,
    Suggestion,
)
from catalog_service.domain.import_job import ImportJob
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ExternalSource
from gv_core.pagination import Page
from gv_core.uow import UnitOfWork
from gv_messaging.ports import TaskQueue

__all__ = [
    "AdminActionLog",
    "CatalogUnitOfWork",
    "ClueStatusRepository",
    "FilmMetadataProvider",
    "ImportFileReader",
    "ImportJobRepository",
    "MediaItemRepository",
    "MusicMetadataProvider",
    "PersonRepository",
    "PrefixCache",
    "RateLimiter",
    "SearchBackend",
    "SearchIndex",
    "TaskQueue",
    "UnitOfWorkFactory",
]


class MediaItemRepository(Protocol):
    """Хранилище агрегатов ``MediaItem``."""

    async def get(self, item_id: uuid.UUID) -> MediaItem | None:
        """Единица по id или ``None``."""
        ...

    async def find_by_external_ref(
        self, source: ExternalSource, external_id: str
    ) -> MediaItem | None:
        """Единица по внешнему идентификатору."""
        ...

    async def exists(self, item_id: uuid.UUID) -> bool:
        """Есть ли единица."""
        ...

    async def add(self, item: MediaItem) -> None:
        """Сохранить новую единицу."""
        ...

    async def save(self, item: MediaItem) -> None:
        """Сохранить изменения.

        Raises:
            ConflictError: единицу изменили параллельно (оптимистичная блокировка).
        """
        ...

    async def list_page(
        self, flt: AdminItemFilter, cursor: str | None, limit: int
    ) -> Page[ItemSummary]:
        """Страница списка для админки (новые первыми)."""
        ...


class PersonRepository(Protocol):
    """Справочник персон."""

    async def resolve(self, key: str, name: str) -> uuid.UUID:
        """Id персоны по стабильному ключу; создаёт персону при первом упоминании."""
        ...


class ImportJobRepository(Protocol):
    """Хранилище заданий импорта."""

    async def get(self, job_id: uuid.UUID) -> ImportJob | None:
        """Задание по id."""
        ...

    async def find_by_key(self, actor_id: uuid.UUID, idempotency_key: str) -> ImportJob | None:
        """Задание, созданное этим пользователем с этим ключом идемпотентности."""
        ...

    async def add(self, job: ImportJob) -> None:
        """Сохранить новое задание."""
        ...

    async def save(self, job: ImportJob) -> None:
        """Сохранить состояние задания."""
        ...


class AdminActionLog(Protocol):
    """Аудит админ-мутаций и их ответы для идемпотентных повторов."""

    async def find(self, actor_id: uuid.UUID, idempotency_key: str) -> RecordedAction | None:
        """Ранее выполненное действие с этим ключом."""
        ...

    async def record(self, action: RecordedAction) -> None:
        """Записать действие (в транзакции мутации).

        Raises:
            ConflictError: ключ занят параллельным запросом.
        """
        ...


class ClueStatusRepository(Protocol):
    """Проекция готовности ассетов загадок."""

    async def get(
        self, media_item_id: uuid.UUID, clue_type: str, profile_key: str
    ) -> ClueStatus | None:
        """Текущее состояние."""
        ...

    async def upsert(self, status: ClueStatus) -> None:
        """Записать состояние."""
        ...


class CatalogUnitOfWork(UnitOfWork, Protocol):
    """Транзакция каталога с репозиториями, привязанными к ней."""

    items: MediaItemRepository
    persons: PersonRepository
    imports: ImportJobRepository
    actions: AdminActionLog
    clue_status: ClueStatusRepository


UnitOfWorkFactory = Callable[[], CatalogUnitOfWork]


class SearchBackend(Protocol):
    """Поиск по каталогу (только активные единицы)."""

    async def autocomplete(self, query: SearchQuery) -> list[Suggestion]:
        """Подсказки по префиксу.

        Raises:
            DependencyUnavailableError: бэкенд недоступен — вызывающий уходит на fallback.
        """
        ...

    async def search(self, query: SearchQuery) -> list[Suggestion]:
        """Полнотекстовый (нечёткий) поиск."""
        ...


class SearchIndex(SearchBackend, Protocol):
    """Поисковый индекс, который наполняется по событиям каталога."""

    async def index(self, doc: SearchDocument) -> bool:
        """Записать документ с внешней версией.

        Returns:
            ``False`` — в индексе уже версия не меньше (устаревшая запись отброшена).
        """
        ...


class PrefixCache(Protocol):
    """Кэш популярных префиксов автокомплита. Ошибки кэша не роняют запрос."""

    async def get(self, key: str) -> list[Suggestion] | None:
        """Закэшированный ответ или ``None``."""
        ...

    async def set(self, key: str, value: list[Suggestion]) -> None:
        """Положить ответ с TTL из настроек."""
        ...

    async def invalidate(self) -> None:
        """Сбросить весь кэш (блокировка, правка алиасов, переиндексация)."""
        ...


class RateLimiter(Protocol):
    """Ограничитель частоты запросов."""

    async def hit(self, key: str) -> bool:
        """Учесть запрос. ``False`` — лимит превышен."""
        ...


class FilmMetadataProvider(Protocol):
    """Источник метаданных фильмов (TMDB)."""

    async def fetch_film(self, ref: str) -> ImportedItem:
        """Фильм по id источника.

        Raises:
            NotFoundError: нет такого фильма.
            DependencyUnavailableError: источник недоступен.
        """
        ...


class MusicMetadataProvider(Protocol):
    """Источник метаданных треков (ADR 0014: только метаданные, без аудио)."""

    async def get_track(self, ref: str) -> ImportedItem:
        """Трек по id источника."""
        ...

    async def search_tracks(self, query: str, limit: int) -> list[ImportedItem]:
        """Поиск треков по тексту (название и/или артист)."""
        ...


class ImportFileReader(Protocol):
    """Чтение файла импорта (JSONL, формат — ``seeds/catalog/README.md``)."""

    def read(self, file_id: str) -> AsyncIterator[ImportedItem | ValueError]:
        """Строки файла: распознанная единица или ошибка строки (не прерывает импорт).

        Raises:
            NotFoundError: файла нет или ``file_id`` недопустим.
        """
        ...
