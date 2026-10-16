"""Чтение: каталог (D10), карточка (D6), черновик автора, «мои», избранное, админка (K6).

Курсоры — keyset ``(значение сортировки, collection_id)`` в непрозрачном base64url
(``gv_core.pagination``); курсор другой сортировки — 422 ``pagination.bad_cursor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from collections_service.application.commands.admin import admin_detail
from collections_service.application.common import cursor_of, detail_json, parse_cursor
from collections_service.application.ports import (
    CatalogQuery,
    CollectionRow,
    Json,
    RateLimiter,
    UnitOfWorkFactory,
)
from collections_service.application.views import Presenter, draft_json
from collections_service.domain.access import Viewer, can_view
from collections_service.domain.difficulty import Difficulty, DifficultyModel
from collections_service.domain.errors import CollectionNotFoundError, CollectionsRateLimitedError
from collections_service.domain.model import Status
from collections_service.domain.policy import MetaConfig
from gv_text.ugc import normalize_ugc

SEARCH_BUCKET = "search"
MINUTE_S = 60
SORT_KINDS: dict[str, type] = {
    "popular": float,
    "new": datetime,
    "likes": int,
    "difficulty": float,
    "featured": int,
}


def sort_value(sort: str, row: CollectionRow) -> object:
    """Значение keyset-сортировки строки (совпадает с выражением ORDER BY репозитория)."""
    if sort == "popular":
        return row.popular_score
    if sort == "new":
        return row.first_published_at
    if sort == "likes":
        return row.likes_count
    if sort == "difficulty":
        return row.difficulty_score if row.difficulty_score is not None else -1.0
    return row.featured_rank if row.featured_rank is not None else 0


@dataclass(frozen=True, slots=True)
class CatalogParams:
    """Параметры ``GET /v1/collections``."""

    q: str | None = None
    domain: str | None = None
    mode: str | None = None
    difficulty: str | None = None
    curated: bool | None = None
    sort: str = "popular"
    cursor: str | None = None
    limit: int = 20


class CollectionQueries:
    """Запросы публичного и admin-API.

    Args:
        uow: Фабрика транзакций (только чтение).
        presenter: JSON.
        difficulty: Корзины сложности (фильтр).
        meta: Словари (``GET /v1/meta``).
        limiter: Лимит поиска (fail-open).
        search_per_min: Запросов с ``q`` в минуту на субъекта.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        presenter: Presenter,
        difficulty: DifficultyModel,
        meta: MetaConfig,
        limiter: RateLimiter,
        *,
        search_per_min: int,
    ) -> None:
        self._uow = uow
        self._presenter = presenter
        self._difficulty = difficulty
        self.meta = meta
        self._limiter = limiter
        self._search_per_min = search_per_min

    # ------------------------------------------------------------------ каталог
    async def catalog(self, params: CatalogParams, *, subject: str) -> Json:
        """``CollectionPage``: опубликованные публичные сборники."""
        text = normalize_ugc(params.q) if params.q else None
        if text:
            retry = await self._limiter.hit(
                SEARCH_BUCKET, subject, limit=self._search_per_min, window_s=MINUTE_S
            )
            if retry is not None:
                raise CollectionsRateLimitedError(retry_after=retry)
        query = CatalogQuery(
            sort=params.sort,
            limit=params.limit + 1,
            text=text or None,
            domain=params.domain,
            mode=params.mode,
            difficulty=(
                self._difficulty.bounds(Difficulty(params.difficulty))
                if params.difficulty
                else None
            ),
            curated=params.curated,
            after=parse_cursor(params.cursor, params.sort, kind=SORT_KINDS[params.sort]),
        )
        async with self._uow() as uow:
            rows = await uow.reads.search(query)
        page, more = rows[: params.limit], len(rows) > params.limit
        last = page[-1] if page and more else None
        return {
            "items": [self._presenter.card(r) for r in page],
            "next_cursor": (
                cursor_of(params.sort, sort_value(params.sort, last), last.collection_id)
                if last
                else None
            ),
        }

    # ------------------------------------------------------------------ карточка
    async def detail(self, collection_id: UUID, viewer: Viewer) -> Json:
        """``CollectionDetail`` (скрытый — только автору и админу, иначе 404).

        Raises:
            CollectionNotFoundError: нет, удалён, не опубликован или не виден (404).
        """
        async with self._uow() as uow:
            row = await uow.reads.row(collection_id)
            if (
                row is None
                or row.content_version == 0
                or not can_view(row.status, row.author_id, viewer)
            ):
                raise CollectionNotFoundError()
            return await detail_json(uow, self._presenter, row, viewer)

    async def draft(self, user_id: UUID, collection_id: UUID) -> Json:
        """``DraftOut`` автора; без черновика — тело опубликованной версии, ``revision = 0``.

        Raises:
            CollectionNotFoundError: нет, удалён или чужой (404).
        """
        async with self._uow() as uow:
            collection = await uow.collections.get(collection_id)
            if (
                collection is None
                or not collection.is_author(user_id)
                or collection.state.status is Status.DELETED
            ):
                raise CollectionNotFoundError()
            return draft_json(collection)

    # ------------------------------------------------------------------ списки
    async def mine(
        self, user_id: UUID, *, status: str | None, cursor: str | None, limit: int
    ) -> Json:
        """``MyCollectionsPage``: свои сборники (кроме удалённых) по ``updated_at``."""
        scope = f"mine:{status or ''}"
        after = parse_cursor(cursor, scope, kind=datetime)
        async with self._uow() as uow:
            rows = await uow.reads.by_author(
                user_id,
                status=Status(status) if status else None,
                after=after,
                limit=limit + 1,
            )
        page, more = rows[:limit], len(rows) > limit
        return {
            "items": [self._presenter.mine(r) for r in page],
            "next_cursor": (
                cursor_of(scope, page[-1].updated_at, page[-1].collection_id) if more else None
            ),
        }

    async def favorites(self, user_id: UUID, *, cursor: str | None, limit: int) -> Json:
        """``CollectionPage``: избранные опубликованные сборники (новые добавления раньше)."""
        after = parse_cursor(cursor, "favorites", kind=datetime)
        async with self._uow() as uow:
            rows = await uow.reads.favorites(user_id, after=after, limit=limit + 1)
        page, more = rows[:limit], len(rows) > limit
        return {
            "items": [self._presenter.card(r) for r, _ in page],
            "next_cursor": (
                cursor_of("favorites", page[-1][1], page[-1][0].collection_id) if more else None
            ),
        }

    # ------------------------------------------------------------------ админка
    async def admin_list(
        self,
        *,
        q: str | None,
        status: str | None,
        kind: str | None,
        cursor: str | None,
        limit: int,
    ) -> Json:
        """``AdminCollectionPage``: все, кроме удалённых; поиск по названию и нику."""
        scope = f"admin:{status or ''}:{kind or ''}"
        after = parse_cursor(cursor, scope, kind=datetime)
        text = normalize_ugc(q) if q else None
        async with self._uow() as uow:
            rows = await uow.reads.admin_list(
                text=text or None,
                status=Status(status) if status else None,
                kind=kind,
                after=after,
                limit=limit + 1,
            )
            page, more = rows[:limit], len(rows) > limit
            authors = {r.author_id for r in page if r.author_id is not None}
            restricted = {a for a in authors if await uow.restrictions.get(a) is not None}
        return {
            "items": [self._presenter.admin(r, restricted=r.author_id in restricted) for r in page],
            "next_cursor": (
                cursor_of(scope, page[-1].updated_at, page[-1].collection_id) if more else None
            ),
        }

    async def admin_detail(self, collection_id: UUID) -> Json:
        """``AdminCollectionDetail``.

        Raises:
            CollectionNotFoundError: нет или удалён (404).
        """
        async with self._uow() as uow:
            return await admin_detail(uow, self._presenter, collection_id)
