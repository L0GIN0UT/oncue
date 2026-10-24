"""Экраны сборников: каталог и страница сборника (план этапа 3b, §9).

Каталог одинаков для всех: collections вызывается без токена, витрины кэшируются. Страница
сборника — личная у вошедшего (состав и ``me`` зависят от субъекта, D6), режимы с
играбельностью берутся из game и гаснут отдельно от карточки.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    CatalogQuery,
    CollectionBlocks,
    CollectionDetail,
    CollectionMe,
    CollectionPage,
    CollectionsBlocks,
    CollectionSort,
    CollectionsPage,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import (
    CollectionsClient,
    GameClient,
    SocialClient,
    UpstreamRejectedError,
)
from bff_web_service.application.queries.common import Limiter, block_of, maybe, screen_error
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
    ) -> None:
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
            return CollectionsPage(
                blocks=CollectionsBlocks(results=block_of(found, "collections.results")),
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
        return CollectionsPage(
            blocks=CollectionsBlocks(
                featured=block_of(featured, "collections.featured"),
                popular=block_of(popular, "collections.popular"),
                new=block_of(new, "collections.new"),
            ),
            cacheable=True,
        )


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
    ) -> None:
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
