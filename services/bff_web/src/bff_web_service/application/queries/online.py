"""Экраны онлайна: хаб и итог матча (план этапа 4, §8; D13, ADR 0017, ADR 0036).

Онлайн — только с аккаунтом (D13): гость и аноним получают на хабе ``auth_required`` без
блоков и без вызовов источников, а на итоге матча — 401/403 как в медиатеке. Каждый блок
деградирует независимо: упавший источник гасит свой блок, страница отвечает 200.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    ItemCard,
    ItemShelves,
    MatchBlocks,
    MatchPage,
    MatchResult,
    OnlineBlocks,
    OnlinePage,
    OnlineStatus,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import (
    CatalogClient,
    GameClient,
    LibraryClient,
    MatchClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import (
    GUEST_FORBIDDEN,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_UNAUTHORIZED,
    Limiter,
    UpstreamError,
    block_of,
    item_card,
    mode_card,
    screen_error,
)
from bff_web_service.domain import Block
from gv_core.errors import ForbiddenError, UnauthenticatedError

log = structlog.get_logger(__name__)

AUTH_REQUIRED = OnlinePage(status=OnlineStatus.AUTH_REQUIRED)


def _is_auth_rejection(value: object) -> bool:
    """match не признал субъекта пользователем: 401 или 403 гостя (непроверенный токен)."""
    return isinstance(value, UpstreamRejectedError) and (
        value.status == HTTP_UNAUTHORIZED
        or (value.status == HTTP_FORBIDDEN and value.code == GUEST_FORBIDDEN)
    )


class GetOnlinePage:
    """Хаб онлайна: открытые лобби, мои последние матчи, онлайн-режимы для создания комнаты."""

    def __init__(
        self,
        match: MatchClient,
        game: GameClient,
        *,
        concurrency: int,
        rooms_limit: int,
        matches_limit: int,
    ) -> None:
        self._match, self._game, self._concurrency = match, game, concurrency
        self._rooms_limit, self._matches_limit = rooms_limit, matches_limit

    async def __call__(self, viewer: Viewer) -> OnlinePage:
        """Собрать хаб.

        Гость (claim ``gst``) и аноним — ``auth_required`` без вызовов. Непроверенный токен
        (JWKS недоступен) уходит в match: его 401 или 403 гостя тоже дают ``auth_required``.
        """
        if viewer.token is None or viewer.kind is SubjectKind.GUEST:
            return AUTH_REQUIRED
        token = viewer.token
        limiter = Limiter(self._concurrency)
        rooms, matches, modes = await asyncio.gather(
            limiter.attempt(self._match.open_rooms(token, limit=self._rooms_limit)),
            limiter.attempt(self._match.my_matches(token, limit=self._matches_limit)),
            limiter.attempt(self._game.online_modes()),
        )
        if _is_auth_rejection(rooms) or _is_auth_rejection(matches):
            return AUTH_REQUIRED
        modes_block = block_of(modes, "online.modes")
        return OnlinePage(
            status=OnlineStatus.OK,
            blocks=OnlineBlocks(
                open_rooms=block_of(rooms, "online.open_rooms"),
                my_matches=block_of(matches, "online.my_matches"),
                modes=Block.ok([mode_card(m) for m in modes_block.data])
                if modes_block.data is not None
                else Block.unavailable(),
            ),
        )


class GetMatchPage:
    """Итог матча: результат из match + карточки единиц раундов (catalog) + флаги медиатеки."""

    def __init__(
        self,
        match: MatchClient,
        catalog: CatalogClient,
        library: LibraryClient,
        *,
        concurrency: int,
    ) -> None:
        self._match, self._catalog, self._library = match, catalog, library
        self._concurrency = concurrency

    async def __call__(self, viewer: Viewer, match_id: UUID) -> MatchPage:
        """Собрать экран.

        Raises:
            UnauthenticatedError: аноним или match не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость (match не вызывается).
            NotFoundError: ``match.match_not_found`` — матча нет или он ещё идёт.
        """
        if viewer.token is None:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError("онлайн доступен только пользователям", code=GUEST_FORBIDDEN)
        token = viewer.token
        limiter = Limiter(self._concurrency)
        try:
            result = await limiter.run(self._match.match(token, match_id))
        except UpstreamUnavailableError as exc:
            log.warning("bff.block_unavailable", block="match", reason=exc.reason)
            return MatchPage(
                MatchBlocks(Block.unavailable(), Block.unavailable(), Block.unavailable())
            )
        except UpstreamRejectedError as exc:
            raise screen_error(exc, not_found="матч не найден") from exc
        ids = _round_item_ids(result)
        items, shelves = await asyncio.gather(
            self._items(limiter, ids), self._shelves(limiter, token, ids)
        )
        return MatchPage(MatchBlocks(match=Block.ok(result), items=items, library=shelves))

    async def _items(self, limiter: Limiter, ids: list[UUID]) -> Block[list[ItemCard]]:
        fetched = await asyncio.gather(*(limiter.attempt(self._catalog.get_item(i)) for i in ids))
        cards: list[ItemCard] = []
        for item_id, value in zip(ids, fetched, strict=True):
            if isinstance(value, UpstreamRejectedError) and value.status == HTTP_NOT_FOUND:
                # Единицу заблокировали после матча — карточки нет, остальные показываем.
                log.info("bff.item_missing", media_item_id=str(item_id))
                continue
            block = block_of(value, "match.items")
            if block.data is None:
                return Block.unavailable()
            cards.append(item_card(block.data))
        return Block.ok(cards)

    async def _shelves(
        self, limiter: Limiter, token: str, ids: list[UUID]
    ) -> Block[list[ItemShelves]]:
        """Флаги «в медиатеке»: library отдаёт полки по одной единице (как кнопка в 3b)."""
        fetched: list[ItemShelves | UpstreamError] = await asyncio.gather(
            *(limiter.attempt(self._library.item_shelves(token, i)) for i in ids)
        )
        flags: list[ItemShelves] = []
        for item_id, value in zip(ids, fetched, strict=True):
            if isinstance(value, UpstreamRejectedError) and value.status == HTTP_NOT_FOUND:
                flags.append(ItemShelves(item_id, []))
                continue
            block = block_of(value, "match.library")
            if block.data is None:
                return Block.unavailable()
            flags.append(block.data)
        return Block.ok(flags)


def _round_item_ids(result: MatchResult) -> list[UUID]:
    """Единицы раундов без повторов, в порядке раундов."""
    return list(dict.fromkeys(r.media_item_id for r in result.rounds))
