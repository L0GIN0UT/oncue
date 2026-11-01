"""Порты: интерфейсы репозиториев и шлюзов, реализуются в infrastructure."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Sequence
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
from catalog_service.domain.clue_texts import ClueTexts
from catalog_service.domain.import_job import ImportJob
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ExternalSource, MediaDomain
from catalog_service.domain.republish import RepublishJob
from gv_core.pagination import Page
from gv_core.uow import UnitOfWork
from gv_db.idempotency import IdempotencyStore
from gv_messaging.ports import TaskQueue

__all__ = [
    "AdminActionLog",
    "CatalogUnitOfWork",
    "ClueStatusRepository",
    "ClueTextsRepository",
    "FilmMetadataProvider",
    "IdempotencyStore",
    "ImportEntrySource",
    "ImportFileReader",
    "ImportJobRepository",
    "IndexMigrator",
    "MediaItemRepository",
    "MusicMetadataProvider",
    "PersonRepository",
    "PrefixCache",
    "RateLimiter",
    "RepublishJobRepository",
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

    async def get_many(self, item_ids: Sequence[uuid.UUID]) -> list[MediaItem]:
        """Существующие единицы из списка (неизвестные пропущены, порядок не гарантирован)."""
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

    async def list_ids_after(
        self, domain: MediaDomain | None, after: uuid.UUID | None, limit: int
    ) -> list[uuid.UUID]:
        """id единиц по возрастанию после ``after`` (порции переопубликации)."""
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
    """Аудит админ-мутаций (кто, что, над чем и почему).

    Идемпотентность повторов — не здесь, а в ``CatalogUnitOfWork.idempotency``.
    """

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


class ClueTextsRepository(Protocol):
    """Тексты загадок (черновики и ревизии, ADR 0024)."""

    async def get(self, media_item_id: uuid.UUID) -> ClueTexts | None:
        """Тексты единицы (строка блокируется до конца транзакции)."""
        ...

    async def save(self, texts: ClueTexts) -> None:
        """Создать или обновить."""
        ...


class RepublishJobRepository(Protocol):
    """Задания переопубликации."""

    async def add(self, job: RepublishJob) -> None:
        """Сохранить новое задание."""
        ...

    async def get(self, job_id: uuid.UUID) -> RepublishJob | None:
        """Задание (строка блокируется до конца транзакции)."""
        ...

    async def save(self, job: RepublishJob) -> None:
        """Сохранить состояние."""
        ...


class CatalogUnitOfWork(UnitOfWork, Protocol):
    """Транзакция каталога с репозиториями, привязанными к ней."""

    # Свойства только для чтения: реализация может объявить более узкий тип атрибута.
    @property
    def items(self) -> MediaItemRepository:
        """Единицы."""
        ...

    @property
    def persons(self) -> PersonRepository:
        """Персоны."""
        ...

    @property
    def imports(self) -> ImportJobRepository:
        """Задания импорта."""
        ...

    @property
    def actions(self) -> AdminActionLog:
        """Аудит админ-мутаций."""
        ...

    @property
    def idempotency(self) -> IdempotencyStore:
        """Ключи идемпотентности admin-мутаций (в этой же транзакции)."""
        ...

    @property
    def clue_status(self) -> ClueStatusRepository:
        """Готовность ассетов."""
        ...

    @property
    def clue_texts(self) -> ClueTextsRepository:
        """Тексты загадок."""
        ...

    @property
    def republish(self) -> RepublishJobRepository:
        """Задания переопубликации."""
        ...


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


class ImportEntrySource(Protocol):
    """Источник единиц для задания импорта (TMDB, файл, музыкальный каталог…).

    Реестр ``Mapping[ImportSource, ImportEntrySource]`` собирается в composition root:
    в нём только включённые и настроенные источники, use-cases импорта по типу источника
    не ветвятся.
    """

    def validate(self, ref: str | None, file_id: str | None) -> None:
        """Проверить параметры запроса импорта до создания задания.

        Raises:
            InvalidInputError: ``catalog.import_bad_ref`` — неверный ``ref``/``file_id``.
        """
        ...

    def entries(self, job: ImportJob) -> AsyncIterator[ImportedItem | ValueError]:
        """Единицы задания: распознанная единица или ошибка строки (не прерывает импорт).

        Raises:
            AppError: источник недоступен, нет такого объекта и т. п. — задание падает
                с ``code`` ошибки.
        """
        ...


class IndexMigrator(Protocol):
    """Пересборка поискового индекса в новый физический индекс с переключением алиаса."""

    @property
    def target(self) -> str:
        """Имя физического индекса, в который собирается поиск."""
        ...

    async def live_indices(self) -> tuple[str, ...]:
        """Физические индексы, на которые сейчас указывает алиас."""
        ...

    async def prepare(self) -> None:
        """Создать целевой индекс, если его нет (идемпотентно)."""
        ...

    async def write(self, docs: Sequence[SearchDocument]) -> int:
        """Записать документы в целевой индекс с внешней версией.

        Returns:
            Сколько документов записано (уже имеющиеся с не меньшей версией пропускаются).
        """
        ...

    async def count(self) -> int:
        """Число документов в целевом индексе (после ``refresh``)."""
        ...

    async def switch(self) -> tuple[str, ...]:
        """Атомарно переключить алиас на целевой индекс; вернуть прежние индексы."""
        ...

    async def rollback(self) -> tuple[str, str]:
        """Атомарно вернуть алиас на предыдущий индекс; вернуть ``(с какого, на какой)``."""
        ...
