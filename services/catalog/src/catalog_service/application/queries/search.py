"""Чтение: автокомплит, поиск, карточка единицы, списки для админки."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from catalog_service.application.commands.imports import import_job_view
from catalog_service.application.dto import (
    AdminItemFilter,
    ItemSummary,
    SearchQuery,
    Suggestion,
    admin_item_view,
)
from catalog_service.application.ports import (
    PrefixCache,
    SearchBackend,
    UnitOfWorkFactory,
)
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import MediaDomain
from catalog_service.domain.normalization import TitleNormalizer
from gv_core.errors import DependencyUnavailableError, InvalidInputError, NotFoundError
from gv_core.pagination import Page

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchLimits:
    """Лимиты запросов (из настроек).

    Attributes:
        max_query_len: Длина ``q`` до нормализации (защита от дорогих запросов).
        min_prefix: Минимальная длина нормализованного префикса автокомплита.
        max_autocomplete: Потолок ``limit`` автокомплита.
        max_search: Потолок ``limit`` поиска.
    """

    max_query_len: int = 64
    min_prefix: int = 2
    max_autocomplete: int = 10
    max_search: int = 20


class _SearchBase:
    def __init__(
        self,
        primary: SearchBackend | None,
        fallback: SearchBackend,
        normalizer: TitleNormalizer,
        limits: SearchLimits,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._normalizer = normalizer
        self._limits = limits

    def _query(
        self, q: str, domain: MediaDomain | None, limit: int, max_limit: int
    ) -> SearchQuery | None:
        if len(q) > self._limits.max_query_len:
            raise InvalidInputError(
                f"запрос длиннее {self._limits.max_query_len} символов",
                code="catalog.query_too_long",
            )
        normalized = self._normalizer.normalize(q)
        if len(normalized) < self._limits.min_prefix:
            return None
        return SearchQuery(normalized, domain, max(1, min(limit, max_limit)))

    async def _with_fallback(self, query: SearchQuery, *, autocomplete: bool) -> list[Suggestion]:
        if self._primary is not None:
            try:
                if autocomplete:
                    return await self._primary.autocomplete(query)
                return await self._primary.search(query)
            except DependencyUnavailableError:
                # Деградация (docs/architecture/services.md): OpenSearch недоступен —
                # отвечаем из Postgres (pg_trgm), а не ошибкой.
                log.warning("catalog.search_primary_unavailable", autocomplete=autocomplete)
        if autocomplete:
            return await self._fallback.autocomplete(query)
        return await self._fallback.search(query)


class Autocomplete(_SearchBase):
    """Подсказки по префиксу названия (только активные единицы)."""

    def __init__(
        self,
        primary: SearchBackend | None,
        fallback: SearchBackend,
        cache: PrefixCache | None,
        normalizer: TitleNormalizer,
        limits: SearchLimits,
    ) -> None:
        super().__init__(primary, fallback, normalizer, limits)
        self._cache = cache

    async def __call__(self, q: str, domain: MediaDomain | None, limit: int) -> list[Suggestion]:
        """Подсказки; слишком короткий префикс — пустой список."""
        query = self._query(q, domain, limit, self._limits.max_autocomplete)
        if query is None:
            return []
        key = f"{query.domain.value if query.domain else 'all'}:{query.limit}:{query.normalized}"
        if self._cache is not None:
            cached = await self._cache.get(key)
            if cached is not None:
                return cached
        result = await self._with_fallback(query, autocomplete=True)
        if self._cache is not None:
            await self._cache.set(key, result)
        return result


class Search(_SearchBase):
    """Нечёткий поиск по названиям и алиасам (только активные единицы)."""

    async def __call__(self, q: str, domain: MediaDomain | None, limit: int) -> list[Suggestion]:
        """Результаты поиска."""
        query = self._query(q, domain, limit, self._limits.max_search)
        if query is None:
            return []
        return await self._with_fallback(query, autocomplete=False)


class GetItem:
    """Карточка единицы."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow = uow_factory

    async def public(self, item_id: uuid.UUID) -> MediaItem:
        """Активная единица; заблокированная неотличима от несуществующей."""
        item = await self._get(item_id)
        if not item.is_active:
            raise NotFoundError("единица не найдена", code="catalog.item_not_found")
        return item

    async def admin(self, item_id: uuid.UUID) -> dict[str, Any]:
        """Карточка для админки (включая заблокированные и источники алиасов)."""
        return admin_item_view(await self._get(item_id))

    async def _get(self, item_id: uuid.UUID) -> MediaItem:
        async with self._uow() as uow:
            item = await uow.items.get(item_id)
        if item is None:
            raise NotFoundError("единица не найдена", code="catalog.item_not_found")
        return item


class ListItems:
    """Список единиц для админки с курсорной пагинацией."""

    def __init__(self, uow_factory: UnitOfWorkFactory, normalizer: TitleNormalizer) -> None:
        self._uow = uow_factory
        self._normalizer = normalizer

    async def __call__(
        self,
        q: str | None,
        domain: MediaDomain | None,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> Page[ItemSummary]:
        """Страница списка."""
        normalized = self._normalizer.normalize(q) if q else None
        flt = AdminItemFilter(normalized_q=normalized or None, domain=domain, status=status)
        async with self._uow() as uow:
            return await uow.items.list_page(flt, cursor, limit)


class GetImport:
    """Состояние задания импорта."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow = uow_factory

    async def __call__(self, import_id: uuid.UUID) -> dict[str, Any]:
        """Представление задания."""
        async with self._uow() as uow:
            job = await uow.imports.get(import_id)
        if job is None:
            raise NotFoundError("задание не найдено", code="catalog.import_not_found")
        return import_job_view(job)
