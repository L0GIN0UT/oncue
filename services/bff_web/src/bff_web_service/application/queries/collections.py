"""Экраны сборников: каталог и страница сборника (план этапа 3b, §9).

Каталог одинаков для всех: collections вызывается без токена, витрины кэшируются. Страница
сборника — личная у вошедшего (состав и ``me`` зависят от субъекта, D6), режимы с
играбельностью берутся из game и гаснут отдельно от карточки.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import replace
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    CatalogQuery,
    CollectionBlocks,
    CollectionDetail,
    CollectionList,
    CollectionMe,
    CollectionPage,
    CollectionsBlocks,
    CollectionSort,
    CollectionsPage,
    MediaLink,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import (
    CollectionsClient,
    GameClient,
    MediaClient,
    SocialClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import (
    Limiter,
    UpstreamError,
    block_of,
    maybe,
    screen_error,
)
from bff_web_service.application.queries.rating import user_token
from bff_web_service.domain import Block

log = structlog.get_logger(__name__)

SHOWCASES: tuple[CollectionSort, ...] = ("featured", "popular", "new")
"""Витрины каталога без ``q``: блок страницы = сортировка collections с тем же именем."""


def collection_me(viewer: Viewer, detail: CollectionDetail | None) -> CollectionMe | None:
    """``me`` страницы: только пользователю (гостю и анониму — ``None``, D7).

    Непроверенный токен (JWKS недоступен) решает collections: гостю он сам вернёт
    ``me: null``.
    """
    if detail is None or viewer.token is None or viewer.kind is SubjectKind.GUEST:
        return None
    return detail.me


class CollectionCovers:
    """Обложки карточек сборников: картинка первой единицы (ADR 0073, вариант A).

    Два пакетных вызова за ответ, последовательно: collections (``cover-items``: id первой
    единицы публичных сборников) и media-pipeline (``cover-links``: ссылки на их artwork).
    collections media-pipeline не вызывает, id единиц наружу не уходят. Любой сбой (токен,
    права, таймаут, breaker, ответ вне контракта) - пустой результат: карточка остаётся без
    картинки (``cover_artwork: null``), страница не падает.
    """

    def __init__(
        self,
        collections: CollectionsClient,
        media: MediaClient | None,
        *,
        batch_max: int,
        deadline_s: float,
    ) -> None:
        self._collections, self._media = collections, media
        self._batch_max, self._deadline_s = batch_max, deadline_s

    async def resolve(self, collection_ids: Iterable[UUID]) -> dict[UUID, MediaLink]:
        """Ссылки на обложки: ``collection_id`` -> картинка (нет картинки - ключа нет)."""
        ids = list(dict.fromkeys(collection_ids))[: self._batch_max]
        if not ids or self._media is None:
            return {}
        try:
            async with asyncio.timeout(self._deadline_s):
                first = await self._collections.cover_items(ids)
                if not first:
                    return {}
                links = await self._media.cover_links(list(dict.fromkeys(first.values())))
        except (UpstreamUnavailableError, UpstreamRejectedError, TimeoutError) as exc:
            log.warning("bff.covers_unavailable", reason=type(exc).__name__)
            return {}
        return {cid: links[mid] for cid, mid in first.items() if mid in links}


class GetCollectionCovers:
    """``POST /v1/collections/covers``: обложки для списков, которые фронт берёт напрямую."""

    def __init__(self, covers: CollectionCovers, *, batch_max: int) -> None:
        self._covers, self.batch_max = covers, batch_max

    async def __call__(self, collection_ids: Sequence[UUID]) -> dict[UUID, MediaLink | None]:
        """Обложка для каждого запрошенного сборника (``None`` - не публичный/нет картинки)."""
        covers = await self._covers.resolve(collection_ids)
        return {cid: covers.get(cid) for cid in dict.fromkeys(collection_ids)}


class GetCollectionsPage:
    """Каталог сборников: витрины ``featured``/``popular``/``new`` или результаты поиска.

    Без ``q`` и курсора — три витрины с одинаковыми фильтрами (по ``showcase_limit``
    карточек, у каждой свой ``next_cursor`` для «показать все»). С ``q`` или курсором —
    только ``results`` в сортировке запроса. Поиск с ``q`` — не для общих кэшей.
    """

    def __init__(
        self,
        collections: CollectionsClient,
        *,
        concurrency: int,
        showcase_limit: int,
        results_limit: int,
        covers: CollectionCovers | None = None,
    ) -> None:
        self._covers = covers
        self._collections, self._concurrency = collections, concurrency
        self._showcase_limit, self._results_limit = showcase_limit, results_limit

    async def __call__(self, query: CatalogQuery) -> CollectionsPage:
        """Собрать каталог (``query.limit`` задаёт сам экран)."""
        limiter = Limiter(self._concurrency)
        if query.q is not None or query.cursor is not None:
            found = await limiter.attempt(
                self._collections.catalog(replace(query, limit=self._results_limit))
            )
            if isinstance(found, UpstreamRejectedError) and (
                error := screen_error(found, not_found="каталог не найден")
            ):
                # Курсор от другой сортировки, неизвестный режим — ошибка запроса, а не отказ.
                raise error
            results = await self._with_covers(found)
            return CollectionsPage(
                blocks=CollectionsBlocks(results=block_of(results[0], "collections.results")),
                cacheable=query.q is None,
            )
        featured, popular, new = await asyncio.gather(
            *(
                limiter.attempt(
                    self._collections.catalog(replace(query, sort=sort, limit=self._showcase_limit))
                )
                for sort in SHOWCASES
            )
        )
        featured, popular, new = await self._with_covers(featured, popular, new)
        return CollectionsPage(
            blocks=CollectionsBlocks(
                featured=block_of(featured, "collections.featured"),
                popular=block_of(popular, "collections.popular"),
                new=block_of(new, "collections.new"),
            ),
            cacheable=True,
        )

    async def _with_covers(
        self, *lists: CollectionList | UpstreamError
    ) -> list[CollectionList | UpstreamError]:
        """Заполнить ``cover_artwork`` во всех списках ОДНИМ пакетным запросом обложек."""
        good = [x for x in lists if isinstance(x, CollectionList)]
        if self._covers is None or not good:
            return list(lists)
        covers = await self._covers.resolve(c.collection_id for x in good for c in x.items)
        if not covers:
            return list(lists)
        return [
            replace(
                x,
                items=[
                    replace(c, cover_artwork=covers[c.collection_id])
                    if c.collection_id in covers
                    else c
                    for c in x.items
                ],
            )
            if isinstance(x, CollectionList)
            else x
            for x in lists
        ]


class GetCollectionPage:
    """Страница сборника: карточка (collections) + режимы с играбельностью (game) + ``me``."""

    def __init__(
        self,
        collections: CollectionsClient,
        game: GameClient,
        *,
        concurrency: int,
        social: SocialClient | None = None,
        comments_limit: int = 30,
        covers: CollectionCovers | None = None,
    ) -> None:
        self._covers = covers
        self._collections, self._game, self._concurrency = collections, game, concurrency
        self._social, self._comments_limit = social, comments_limit

    async def __call__(self, viewer: Viewer, collection_id: UUID) -> CollectionPage:
        """Собрать страницу.

        Raises:
            NotFoundError: ``collections.not_found`` — нет, скрыт для чужих или удалён
                (решает collections; game при этом не важен).
            UnauthenticatedError / ForbiddenError: collections не принял токен.
        """
        limiter = Limiter(self._concurrency)
        # Комментарии читают и анонимы; токен нужен только ради `can_edit`/`can_delete`.
        # Гостю и отвергнутому токену он не уходит. Скрытый сборник отсекает 404 карточки.
        comments_call = (
            limiter.attempt(
                self._social.comments(user_token(viewer), collection_id, limit=self._comments_limit)
            )
            if self._social is not None
            else None
        )
        detail, modes, comments = await asyncio.gather(
            limiter.attempt(self._collections.detail(viewer.token, collection_id)),
            limiter.attempt(self._game.collection_modes(collection_id)),
            maybe(comments_call),
        )
        if isinstance(detail, UpstreamRejectedError) and (
            error := screen_error(detail, not_found="сборник не найден")
        ):
            raise error
        if self._covers is not None and isinstance(detail, CollectionDetail):
            artwork = (await self._covers.resolve([detail.collection_id])).get(detail.collection_id)
            if artwork is not None:
                detail = replace(detail, cover_artwork=artwork)
        collection: Block[CollectionDetail] = block_of(detail, "collection")
        modes_block = block_of(modes, "collection.modes")
        comments_block = None if comments is None else block_of(comments, "collection.comments")
        return CollectionPage(
            blocks=CollectionBlocks(
                collection=collection, modes=modes_block, comments=comments_block
            ),
            personal=viewer.is_personal,
            me=collection_me(viewer, collection.data),
        )
