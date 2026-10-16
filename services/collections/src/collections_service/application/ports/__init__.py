"""Порты application-слоя collections: репозитории, Unit of Work, лимитер.

Реализации — ``infrastructure``. Фейков для use-cases нет намеренно (как в game): правила
покрыты unit-тестами домена, а use-cases — component-тестами на реальном Postgres
(блокировки, уникальные ключи и курсоры — часть поведения).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from collections_service.domain.collection import Collection
from collections_service.domain.model import Status
from collections_service.domain.policy import ItemInfo
from gv_core.domain import DomainEvent
from gv_core.uow import UnitOfWork
from gv_db.idempotency import IdempotencyStore

type Json = dict[str, Any]


# ---------------------------------------------------------------- модели чтения
@dataclass(frozen=True, slots=True)
class CollectionRow:
    """Строка сборника для карточек, списков и админки (без тела версии)."""

    collection_id: UUID
    domain: str
    kind: str
    status: Status
    visibility: str
    title: str
    description: str | None
    tags: tuple[str, ...]
    cover: str | None
    author_id: UUID | None
    nickname: str | None
    default_mode_key: str | None
    content_version: int
    items_count: int
    likes_count: int
    plays_count: int
    difficulty_score: float | None
    featured: bool
    featured_rank: int | None
    popular_score: float
    hidden_reason: str | None
    hidden_by: UUID | None
    hidden_at: datetime | None
    draft_revision: int | None
    draft_items: int | None
    created_at: datetime
    updated_at: datetime
    first_published_at: datetime | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class ItemRow:
    """Единица состава с карточкой из проекции каталога."""

    position: int
    media_item_id: UUID
    title: str | None
    year: int | None
    status: str | None
    emoji: str | None


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """Запрос каталога (D10): только ``published`` + ``public``.

    Attributes:
        text: Нормализованный ``q`` (поиск по названию, описанию и словам тегов).
        difficulty: Полуинтервал ``[low, high)`` сложности корзины.
        after: Keyset последнего элемента: ``(значение сортировки, collection_id)``.
    """

    sort: str
    limit: int
    text: str | None = None
    domain: str | None = None
    mode: str | None = None
    difficulty: tuple[float, float] | None = None
    curated: bool | None = None
    after: tuple[Any, UUID] | None = None


@dataclass(frozen=True, slots=True)
class CatalogItemSnapshot:
    """Единица каталога для проекции ``catalog_items_proj``."""

    media_item_id: UUID
    domain: str
    status: str
    version: int
    title: str
    year: int | None


@dataclass(frozen=True, slots=True)
class Restriction:
    """Ограничение автора модератором."""

    user_id: UUID
    reason: str
    actor_id: UUID | None
    at: datetime


@dataclass(frozen=True, slots=True)
class AdminAction:
    """Строка аудита admin-действия (без UGC-текста: ревизии и статусы)."""

    actor_id: UUID
    action: str
    target: str
    reason: str
    at: datetime
    details: Json = field(default_factory=dict)


# ---------------------------------------------------------------- репозитории
class CollectionRepository(Protocol):
    """Агрегаты сборников (запись) и выборки для фоновых процессов."""

    async def get(self, collection_id: UUID, *, for_update: bool = False) -> Collection | None:
        """Агрегат с черновиком и текущей версией (``for_update`` — ``FOR UPDATE``)."""
        ...

    async def add(self, collection: Collection) -> None:
        """Вставить новый агрегат (и его первую версию, если она есть)."""
        ...

    async def save(self, collection: Collection) -> None:
        """Сохранить агрегат: строку, черновик, новую версию состава."""
        ...

    async def purge(self, collection_id: UUID) -> None:
        """Удалить сборник целиком (ни разу не опубликованный черновик удалённого автора)."""
        ...

    async def lock_author(self, author_id: UUID) -> None:
        """Сериализовать создание сборников автора (advisory lock транзакции)."""
        ...

    async def count_drafts(self, author_id: UUID) -> int:
        """Ни разу не опубликованных сборников у автора (лимит ``drafts_max``)."""
        ...

    async def ids_by_author(self, author_id: UUID) -> list[UUID]:
        """Все сборники автора (включая удалённые)."""
        ...

    async def ids_with_item(self, media_item_id: UUID, limit: int) -> list[UUID]:
        """Опубликованные/скрытые сборники, в текущем составе которых есть единица."""
        ...

    async def blocked_pairs(self, limit: int) -> list[tuple[UUID, UUID]]:
        """Пары ``(collection_id, media_item_id)``: в составе заблокированная единица."""
        ...

    async def like_added(self, collection_id: UUID) -> tuple[int, int]:
        """``likes_count + 1`` и ``version + 1`` атомарно; ``(version, likes_count)``."""
        ...

    async def like_removed(self, collection_id: UUID) -> int:
        """``likes_count − 1``; новое значение счётчика."""
        ...

    async def likes_count(self, collection_id: UUID) -> int:
        """Текущий счётчик лайков."""
        ...


class ReactionRepository(Protocol):
    """Лайки и избранное: PK ``(user_id, collection_id)`` — естественная идемпотентность."""

    async def add_like(self, user_id: UUID, collection_id: UUID, now: datetime) -> bool:
        """``True`` — лайк новый."""
        ...

    async def remove_like(self, user_id: UUID, collection_id: UUID) -> bool:
        """``True`` — лайк был."""
        ...

    async def add_favorite(self, user_id: UUID, collection_id: UUID, now: datetime) -> bool:
        """``True`` — добавлено сейчас."""
        ...

    async def remove_favorite(self, user_id: UUID, collection_id: UUID) -> bool:
        """``True`` — было в избранном."""
        ...

    async def state(self, user_id: UUID, collection_id: UUID) -> tuple[bool, bool]:
        """``(liked, favorite)``."""
        ...

    async def forget_user(self, user_id: UUID) -> int:
        """Удалить лайки (со снижением счётчиков) и избранное пользователя; число лайков."""
        ...


class StatsRepository(Protocol):
    """Завершения, «пройден», сложность и дневная статистика популярности."""

    async def add_completion(
        self,
        user_id: UUID,
        collection_id: UUID,
        content_version: int,
        *,
        ratio: float | None,
        session_id: UUID,
        at: datetime,
    ) -> bool:
        """Первое завершение пользователя на версию; ``False`` — уже было."""
        ...

    async def completed(self, user_id: UUID, collection_id: UUID, content_version: int) -> bool:
        """Есть завершение версии."""
        ...

    async def add_play(self, collection_id: UUID, ratio: float | None) -> tuple[int, float, int]:
        """``plays_count + 1`` и сумма долей; ``(plays_count, ratio_sum, ratio_count)``."""
        ...

    async def set_difficulty(self, collection_id: UUID, score: float | None) -> None:
        """Сохранить сложность."""
        ...

    async def bump_daily(self, collection_id: UUID, day: date, *, likes: int, plays: int) -> None:
        """Прибавить к дневной статистике (окно популярности)."""
        ...

    async def recompute_popular(
        self, since: date, *, like_weight: float, play_weight: float, after: UUID | None, limit: int
    ) -> UUID | None:
        """Пересчитать ``popular_score`` порции сборников после ``after``; последний id."""
        ...

    async def purge_daily(self, before: date) -> int:
        """Удалить дневную статистику до ``before``."""
        ...

    async def forget_user(self, user_id: UUID) -> int:
        """Удалить завершения пользователя (агрегаты сложности остаются)."""
        ...


class ProjectionRepository(Protocol):
    """Проекции catalog (единицы) и identity (ники)."""

    async def items_info(self, ids: Sequence[UUID]) -> dict[UUID, ItemInfo]:
        """Домен и статус единиц (отсутствующих в проекции нет в ответе)."""
        ...

    async def item_version(self, media_item_id: UUID) -> int | None:
        """Применённая версия событий единицы (под блокировкой строки)."""
        ...

    async def upsert_item(self, item: CatalogItemSnapshot) -> None:
        """Создать или заменить единицу."""
        ...

    async def set_item_status(self, media_item_id: UUID, status: str, version: int) -> None:
        """Сменить статус единицы."""
        ...

    async def item_rows(self, items: Sequence[tuple[UUID, str | None]]) -> list[ItemRow]:
        """Карточки единиц в заданном порядке (``(media_item_id, emoji)``)."""
        ...

    async def user_version(self, user_id: UUID) -> int | None:
        """Применённая версия событий пользователя (под блокировкой строки)."""
        ...

    async def upsert_user(self, user_id: UUID, nickname: str | None, version: int) -> None:
        """Ник пользователя (``None`` — удалён)."""
        ...


class RestrictionRepository(Protocol):
    """Ограничения авторов (локальная таблица, D8)."""

    async def get(self, user_id: UUID) -> Restriction | None:
        """Ограничение или ``None``."""
        ...

    async def put(self, restriction: Restriction) -> None:
        """Создать или заменить."""
        ...

    async def delete(self, user_id: UUID) -> bool:
        """Снять; ``False`` — не было."""
        ...


class AdminAuditLog(Protocol):
    """Аудит admin-действий."""

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        ...


class CatalogReads(Protocol):
    """Чтение карточек и списков (keyset-курсоры)."""

    async def row(self, collection_id: UUID) -> CollectionRow | None:
        """Строка сборника (кроме удалённых)."""
        ...

    async def items(self, collection_id: UUID) -> list[ItemRow]:
        """Текущий опубликованный состав с карточками единиц."""
        ...

    async def search(self, query: CatalogQuery) -> list[CollectionRow]:
        """Каталог: до ``query.limit`` строк по keyset после ``query.after``."""
        ...

    async def favorites(
        self, user_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[tuple[CollectionRow, datetime]]:
        """Избранные опубликованные сборники по времени добавления (новые раньше)."""
        ...

    async def by_author(
        self,
        author_id: UUID,
        *,
        status: Status | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[CollectionRow]:
        """Сборники автора (кроме удалённых) по ``updated_at`` (новые раньше)."""
        ...

    async def admin_list(
        self,
        *,
        text: str | None,
        status: Status | None,
        kind: str | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[CollectionRow]:
        """Все сборники (кроме удалённых) с поиском по названию и нику автора."""
        ...


# ---------------------------------------------------------------- Unit of Work
class CollectionsUnitOfWork(UnitOfWork, Protocol):
    """Транзакция use-case с репозиториями collections."""

    collections: CollectionRepository
    reactions: ReactionRepository
    stats: StatsRepository
    projection: ProjectionRepository
    restrictions: RestrictionRepository
    audit: AdminAuditLog
    reads: CatalogReads
    idempotency: IdempotencyStore

    def emit(self, event: DomainEvent) -> None:
        """Событие без агрегата (лайк — атомарный UPDATE счётчика) — в outbox при коммите."""
        ...


UnitOfWorkFactory = Callable[[], CollectionsUnitOfWork]


class RateLimiter(Protocol):
    """Лимит частоты (фиксированное окно).

    ``fail_closed`` — недоступный лимитер отклоняет запрос (публикация, §12 п. 4), иначе
    пропускает с метрикой (лайки, избранное, поиск).
    """

    async def hit(
        self, bucket: str, subject: str, *, limit: int, window_s: int, fail_closed: bool = False
    ) -> int | None:
        """Учесть запрос; ``None`` — лимит не превышен, иначе секунды до нового окна.

        Raises:
            LimiterUnavailableError: лимитер недоступен при ``fail_closed``.
        """
        ...
