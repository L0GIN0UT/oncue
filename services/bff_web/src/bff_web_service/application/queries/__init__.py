"""Запросы экранов: параллельные вызовы источников и сборка блоков (план этапа 2, §6).

Каждый источник — отдельный блок: таймаут или отказ одного гасит только его блок, страница
отвечает 200. Исключение — экран результатов: без итога сессии показывать нечего, поэтому
осознанный отказ game (401/403/404/409) становится ответом экрана.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable, Sequence
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    DailyCard,
    DailyStatus,
    Domain,
    HomeBlocks,
    HomePage,
    Item,
    ItemCard,
    Mode,
    ModeCard,
    Person,
    ResultRoundCard,
    ResultsBlocks,
    ResultsPage,
    ResultSummary,
    SessionResult,
    SessionView,
    SoloBlocks,
    SoloPage,
    SubjectKind,
    SubjectView,
    Viewer,
)
from bff_web_service.application.ports import (
    CatalogClient,
    GameClient,
    IdentityClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.domain import Block
from gv_core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthenticatedError

log = structlog.get_logger(__name__)

GUEST_FORBIDDEN = "auth.guest_forbidden"
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409

type UpstreamError = UpstreamUnavailableError | UpstreamRejectedError


class Limiter:
    """Ограничение одновременных вызовов источников в рамках одного запроса экрана."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit >= 1")
        self._semaphore = asyncio.Semaphore(limit)

    async def run[T](self, call: Awaitable[T]) -> T:
        """Выполнить вызов, когда освободится слот."""
        async with self._semaphore:
            return await call

    async def attempt[T](self, call: Awaitable[T]) -> T | UpstreamError:
        """Выполнить вызов; ошибка источника возвращается значением (для ``gather``)."""
        try:
            return await self.run(call)
        except (UpstreamUnavailableError, UpstreamRejectedError) as exc:
            return exc


_ANONYMOUS = UpstreamRejectedError("game", _HTTP_UNAUTHORIZED, "auth.unauthenticated")


async def _personal_daily(
    limiter: Limiter, game: GameClient, viewer: Viewer, domain: Domain | None
) -> list[DailyStatus] | UpstreamError:
    """Дейлики субъекта; аноним (нет токена) — без вызова game.

    game отвечает анониму 401: статус дейлика есть только у субъекта (гостя или
    пользователя). Фронтенд сначала получает гостевую сессию, затем перезапрашивает блок.
    """
    if viewer.token is None:
        return _ANONYMOUS
    return await limiter.attempt(game.daily(viewer.token, domain))


def _block[T](value: T | UpstreamError, block: str) -> Block[T]:
    if value is _ANONYMOUS:
        return Block.unavailable()
    if isinstance(value, UpstreamUnavailableError | UpstreamRejectedError):
        log.warning(
            "bff.block_unavailable",
            block=block,
            service=value.service,
            reason=value.reason if isinstance(value, UpstreamUnavailableError) else value.code,
        )
        return Block.unavailable()
    return Block.ok(value)


# ----------------------------------------------------------------------------- сборка
def mode_card(mode: Mode) -> ModeCard:
    """Режим → карточка."""
    return ModeCard(
        key=mode.key,
        domain=mode.domain,
        title_key=mode.title_key,
        source_type=mode.source_type,
        clue_type=mode.clue_type,
        rounds=mode.rounds,
        allow_guest=mode.allow_guest,
        presets=mode.presets,
    )


def daily_cards(modes: Sequence[Mode], statuses: Iterable[DailyStatus]) -> list[DailyCard]:
    """Статусы дейликов + режимы → карточки (порядок — как у game).

    Статус режима, которого нет среди опубликованных (архивирован между вызовами),
    пропускается: без названия и домена карточку не нарисовать.
    """
    by_key = {m.key: m for m in modes}
    cards: list[DailyCard] = []
    for status in statuses:
        mode = by_key.get(status.mode_key)
        if mode is None:
            log.info("bff.daily_without_mode", mode_key=status.mode_key)
            continue
        cards.append(
            DailyCard(
                mode_key=status.mode_key,
                domain=mode.domain,
                title_key=mode.title_key,
                day=status.day,
                status=status.status,
                max_score=status.max_score,
                next_reset_at=status.next_reset_at,
                session_id=status.session_id,
                score=status.score,
            )
        )
    return cards


def result_summary(session_id: UUID, result: SessionResult) -> ResultSummary:
    """Итог game → итог экрана (доля угадавших — по индексу раунда)."""
    pct = {s.index: s.exact_pct for s in result.daily_stats or ()}
    return ResultSummary(
        session_id=session_id,
        score=result.score,
        max_score=result.max_score,
        share_text=result.share.text,
        rounds=[
            ResultRoundCard(
                index=r.index,
                outcome=r.outcome,
                level=r.level,
                points=r.points,
                media_item_id=r.reveal.media_item_id if r.reveal else None,
                exact_pct=pct.get(r.index),
            )
            for r in result.rounds
        ],
    )


def item_card(item: Item) -> ItemCard:
    """Карточка catalog → карточка экрана."""
    return ItemCard(
        media_item_id=item.id,
        domain=item.domain,
        kind=item.kind,
        title=item.title,
        original_title=item.original_title,
        year=item.year,
        genres=list(item.genres),
        persons=[Person(name=p.name, role=p.role) for p in item.persons],
    )


def _revealed_ids(result: SessionResult) -> list[UUID]:
    seen: dict[UUID, None] = {}
    for r in result.rounds:
        if r.reveal is not None:
            seen.setdefault(r.reveal.media_item_id, None)
    return list(seen)


# ----------------------------------------------------------------------------- запросы
class GetSession:
    """Кто смотрит страницу (``GET /v1/session``).

    Гость определяется по claim проверенного токена (identity отвечает гостю на ``/v1/me``
    403). Имя пользователя — из identity; недоступен identity — субъект без имени.
    """

    def __init__(self, identity: IdentityClient) -> None:
        self._identity = identity

    async def __call__(self, viewer: Viewer) -> SessionView:
        """Субъект или ``None`` для анонима и отозванной сессии."""
        if viewer.token is None:
            return SessionView(subject=None)
        if viewer.kind is SubjectKind.GUEST:
            return SessionView(subject=SubjectView(kind=SubjectKind.GUEST))
        return SessionView(subject=await self._subject(viewer.token, viewer.kind))

    async def _subject(self, token: str, kind: SubjectKind | None) -> SubjectView | None:
        try:
            me = await self._identity.me(token)
        except UpstreamUnavailableError as exc:
            log.warning("bff.identity_unavailable", reason=exc.reason)
            # Проверенный пользователь — без имени; непроверенный токен — неизвестно, кто это.
            return SubjectView(kind=SubjectKind.USER) if kind is SubjectKind.USER else None
        except UpstreamRejectedError as exc:
            if exc.status == _HTTP_FORBIDDEN and exc.code == GUEST_FORBIDDEN:
                return SubjectView(kind=SubjectKind.GUEST)
            # 401: сессию отозвали после выдачи access-токена — для экрана это аноним.
            return None
        return SubjectView(kind=SubjectKind.USER, display_name=me.display_name)


class GetHomePage:
    """Главная: дейлики субъекта и все опубликованные режимы."""

    def __init__(self, game: GameClient, *, concurrency: int) -> None:
        self._game, self._concurrency = game, concurrency

    async def __call__(self, viewer: Viewer) -> HomePage:
        """Собрать главную."""
        limiter = Limiter(self._concurrency)
        modes, daily = await asyncio.gather(
            limiter.attempt(self._game.list_modes()),
            _personal_daily(limiter, self._game, viewer, None),
        )
        modes_block = _block(modes, "modes")
        daily_block = _block(daily, "dailies")
        dailies: Block[list[DailyCard]] = (
            Block.ok(daily_cards(modes_block.data, daily_block.data))
            if modes_block.data is not None and daily_block.data is not None
            else Block.unavailable()
        )
        return HomePage(
            blocks=HomeBlocks(
                dailies=dailies,
                modes=Block.ok([mode_card(m) for m in modes_block.data])
                if modes_block.data is not None
                else Block.unavailable(),
            ),
            personal=viewer.is_personal,
        )


class GetSoloPage:
    """Хаб соло-режимов: дейлики и случайные режимы домена."""

    def __init__(
        self, game: GameClient, *, concurrency: int, random_source_types: Iterable[str]
    ) -> None:
        self._game, self._concurrency = game, concurrency
        self._random_types = frozenset(random_source_types)

    async def __call__(self, viewer: Viewer, domain: Domain | None) -> SoloPage:
        """Собрать хаб (``domain = None`` — оба домена)."""
        limiter = Limiter(self._concurrency)
        modes, daily = await asyncio.gather(
            limiter.attempt(self._game.list_modes()),
            _personal_daily(limiter, self._game, viewer, domain),
        )
        modes_block = _block(modes, "modes")
        daily_block = _block(daily, "daily")
        in_domain = (
            [m for m in modes_block.data if domain is None or m.domain == domain]
            if modes_block.data is not None
            else None
        )
        return SoloPage(
            blocks=SoloBlocks(
                daily=Block.ok(daily_cards(in_domain, daily_block.data))
                if in_domain is not None and daily_block.data is not None
                else Block.unavailable(),
                random=Block.ok(
                    [mode_card(m) for m in in_domain if m.source_type in self._random_types]
                )
                if in_domain is not None
                else Block.unavailable(),
            ),
            personal=viewer.is_personal,
        )


class GetResultsPage:
    """Экран результатов: итог из game + карточки catalog по ревилам."""

    def __init__(self, game: GameClient, catalog: CatalogClient, *, concurrency: int) -> None:
        self._game, self._catalog, self._concurrency = game, catalog, concurrency

    async def __call__(self, viewer: Viewer, session_id: UUID) -> ResultsPage:
        """Собрать экран.

        Raises:
            UnauthenticatedError: нет токена или game его не принял.
            ForbiddenError / NotFoundError / ConflictError: отказ game (чужая или
                несуществующая сессия — 404; сессия не завершена — 409).
        """
        if viewer.token is None:
            raise UnauthenticatedError("требуется вход")
        limiter = Limiter(self._concurrency)
        try:
            result = await limiter.run(self._game.session_result(viewer.token, session_id))
        except UpstreamUnavailableError as exc:
            log.warning("bff.block_unavailable", block="result", reason=exc.reason)
            return ResultsPage(ResultsBlocks(Block.unavailable(), Block.unavailable()))
        except UpstreamRejectedError as exc:
            raise _rejection(exc) from exc
        return ResultsPage(
            ResultsBlocks(
                result=Block.ok(result_summary(session_id, result)),
                items=await self._items(limiter, _revealed_ids(result)),
            )
        )

    async def _items(self, limiter: Limiter, ids: list[UUID]) -> Block[list[ItemCard]]:
        fetched = await asyncio.gather(*(limiter.attempt(self._catalog.get_item(i)) for i in ids))
        cards: list[ItemCard] = []
        for item_id, value in zip(ids, fetched, strict=True):
            if isinstance(value, UpstreamRejectedError) and value.status == _HTTP_NOT_FOUND:
                # Единицу заблокировали после игры — карточки нет, остальные показываем.
                log.info("bff.item_missing", media_item_id=str(item_id))
                continue
            block = _block(value, "items")
            if block.data is None:
                return Block.unavailable()
            cards.append(item_card(block.data))
        return Block.ok(cards)


def _rejection(exc: UpstreamRejectedError) -> Exception:
    message = "game отклонил запрос"
    if exc.status == _HTTP_UNAUTHORIZED:
        return UnauthenticatedError(message, code=exc.code)
    if exc.status == _HTTP_FORBIDDEN:
        return ForbiddenError(message, code=exc.code)
    if exc.status == _HTTP_CONFLICT:
        return ConflictError(message, code=exc.code)
    # 404 и прочие 4xx — «нет такой сессии»: не раскрываем, почему (IDOR, §9 п.2).
    code = exc.code if exc.status == _HTTP_NOT_FOUND else NotFoundError.default_code
    return NotFoundError("сессия не найдена", code=code)
