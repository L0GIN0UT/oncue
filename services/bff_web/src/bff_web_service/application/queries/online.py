"""Экраны онлайна: хаб и итог матча (план этапа 4, §8; D13, ADR 0017, ADR 0036).

Онлайн — только с аккаунтом (D13): гость и аноним получают на хабе ``auth_required`` без
блоков и без вызовов источников, а на итоге матча — 401/403 как в медиатеке. Каждый блок
деградирует независимо: упавший источник гасит свой блок, страница отвечает 200.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
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
SESSION_EXPIRED = OnlinePage(status=OnlineStatus.SESSION_EXPIRED)


def _is_unauthenticated(value: object) -> bool:
    """match не принял токен (истёк, отозван): фронту — обновить сессию."""
    return isinstance(value, UpstreamRejectedError) and value.status == HTTP_UNAUTHORIZED


def _is_guest(value: object) -> bool:
    """match распознал гостя по непроверенному токену (JWKS был недоступен)."""
    return (
        isinstance(value, UpstreamRejectedError)
        and value.status == HTTP_FORBIDDEN
        and value.code == GUEST_FORBIDDEN
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

        Гость (claim ``gst``) и аноним — ``auth_required`` без вызовов; токен, не прошедший
        локальную проверку, — ``session_expired`` без вызовов. Непроверенный токен (JWKS
        недоступен) уходит в match: его 401 даёт ``session_expired``, 403 гостя —
        ``auth_required``.
        """
        if viewer.token is None:
            return SESSION_EXPIRED if viewer.token_rejected else AUTH_REQUIRED
        if viewer.kind is SubjectKind.GUEST:
            return AUTH_REQUIRED
        token = viewer.token
        limiter = Limiter(self._concurrency)
        rooms, matches, modes = await asyncio.gather(
            limiter.attempt(self._match.open_rooms(token, limit=self._rooms_limit)),
            limiter.attempt(self._match.my_matches(token, limit=self._matches_limit)),
            limiter.attempt(self._game.online_modes()),
        )
        if _is_unauthenticated(rooms) or _is_unauthenticated(matches):
            return SESSION_EXPIRED
        if _is_guest(rooms) or _is_guest(matches):
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
        items_deadline_s: float,
    ) -> None:
        self._match, self._catalog, self._library = match, catalog, library
        self._concurrency, self._deadline_s = concurrency, items_deadline_s

    async def __call__(self, viewer: Viewer, match_id: UUID) -> MatchPage:
        """Собрать экран.

        Raises:
            UnauthenticatedError: аноним или match не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость (match не вызывается).
            NotFoundError: ``match.match_not_found`` — матча нет или он ещё идёт.

        Прочие 4xx match (не описанные в контракте) — сбой источника: все блоки
        ``unavailable``, страница 200.
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
            error = screen_error(exc, not_found="матч не найден")
            if error is not None:
                raise error from exc
            log.warning("bff.block_unavailable", block="match", reason=exc.code)
            return MatchPage(
                MatchBlocks(Block.unavailable(), Block.unavailable(), Block.unavailable())
            )
        ids = _round_item_ids(result)
        # Общий предел обоих блоков: до 50 единиц идут волнами по concurrency, и без него
        # страница ждала бы «волны × timeout_s». Блок, не успевший к сроку, гаснет сам.
        deadline = asyncio.get_running_loop().time() + self._deadline_s
        items, shelves = await asyncio.gather(
            _until(deadline, self._items(limiter, ids), "match.items"),
            _until(deadline, self._shelves(limiter, token, ids), "match.library"),
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


async def _until[T](deadline: float, call: Awaitable[Block[T]], block: str) -> Block[T]:
    """Блок к сроку ``deadline`` (время цикла событий) или ``unavailable``."""
    try:
        async with asyncio.timeout_at(deadline):
            return await call
    except TimeoutError:
        log.warning("bff.block_unavailable", block=block, reason="deadline")
        return Block.unavailable()


def _round_item_ids(result: MatchResult) -> list[UUID]:
    """Единицы раундов без повторов, в порядке раундов."""
    return list(dict.fromkeys(r.media_item_id for r in result.rounds))
