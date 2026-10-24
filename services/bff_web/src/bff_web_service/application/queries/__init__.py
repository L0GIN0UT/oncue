"""Запросы экранов: параллельные вызовы источников и сборка блоков (план этапа 2, §6).

Каждый источник — отдельный блок: таймаут или отказ одного гасит только его блок, страница
отвечает 200. Исключение — экран результатов: без итога сессии показывать нечего, поэтому
осознанный отказ game (401/403/404/409) становится ответом экрана.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    DailyCard,
    DailyStatus,
    Domain,
    HomeBlocks,
    HomePage,
    ItemCard,
    Mode,
    ModeCard,
    ModeGroup,
    PersonalRecord,
    Presentation,
    RecordCard,
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
from bff_web_service.application.queries.collections import (
    GetCollectionPage,
    GetCollectionsPage,
    collection_me,
)
from bff_web_service.application.queries.common import (
    ANONYMOUS_SKIP,
    GUEST_FORBIDDEN,
    HTTP_CONFLICT,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_UNAUTHORIZED,
    Limiter,
    UpstreamError,
    block_of,
    item_card,
    mode_card,
)
from bff_web_service.application.queries.events import EventsContext, GetEventPage
from bff_web_service.application.queries.library import GetLibraryPage, GetLibraryShelfPage
from bff_web_service.application.queries.online import GetMatchPage, GetOnlinePage
from bff_web_service.application.queries.progression import (
    GetPlayerPage,
    GetProfilePage,
    GetShellPage,
    GetShopPage,
    HomeProgress,
)
from bff_web_service.application.queries.rating import GetLeaderboardsPage
from bff_web_service.domain import Block
from gv_core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthenticatedError

log = structlog.get_logger(__name__)

__all__ = [
    "GUEST_FORBIDDEN",
    "EventsContext",
    "GetCollectionPage",
    "GetCollectionsPage",
    "GetEventPage",
    "GetHomePage",
    "GetLeaderboardsPage",
    "GetLibraryPage",
    "GetLibraryShelfPage",
    "GetMatchPage",
    "GetOnlinePage",
    "GetPlayerPage",
    "GetProfilePage",
    "GetResultsPage",
    "GetSession",
    "GetShellPage",
    "GetShopPage",
    "GetSoloPage",
    "HomeProgress",
    "Limiter",
    "PageLayout",
    "collection_me",
    "daily_cards",
    "mode_card",
    "mode_groups",
    "period_cards",
    "record_cards",
    "result_summary",
]


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Как режимы раскладываются по блокам экранов — по данным game, без ключей режимов.

    Attributes:
        random_source_types: Типы Source блока «случайный» хаба (витрина этапа 2).
        scheduled_source_types: Типы Source плановых режимов: их показывают блоки периода
            (``daily``/``weekly``), а не витрина ``modes``.
        weekly_source_types: Плановые типы с недельным периодом (блок ``weekly``); статусы
            ``/v1/daily`` остальных типов — дейлики.
        collection_source_types: Типы Source режимов сборника: стартуют только со страницы
            сборника (нужен ``collection_id``), поэтому в витрины не попадают. game их в
            ``/v1/modes`` и так не отдаёт — фильтр страхует от смены этого поведения.
    """

    random_source_types: frozenset[str] = frozenset({"random"})
    scheduled_source_types: frozenset[str] = frozenset({"daily", "bracket"})
    weekly_source_types: frozenset[str] = frozenset({"bracket"})
    collection_source_types: frozenset[str] = frozenset({"collection"})

    @property
    def hidden_from_showcase(self) -> frozenset[str]:
        """Типы Source, которых нет в витрине ``modes`` хаба: плановые и сборники."""
        return self.scheduled_source_types | self.collection_source_types

    def listed(self, modes: Iterable[Mode]) -> list[Mode]:
        """Режимы, которые можно показывать в списках (без режимов сборника)."""
        return [m for m in modes if m.source_type not in self.collection_source_types]

    @classmethod
    def of(
        cls,
        *,
        random_source_types: Iterable[str],
        scheduled_source_types: Iterable[str],
        weekly_source_types: Iterable[str],
        collection_source_types: Iterable[str] = ("collection",),
    ) -> PageLayout:
        """Раскладка из настроек (кортежи ``GV_PAGES__*``)."""
        return cls(
            random_source_types=frozenset(random_source_types),
            scheduled_source_types=frozenset(scheduled_source_types),
            weekly_source_types=frozenset(weekly_source_types),
            collection_source_types=frozenset(collection_source_types),
        )


async def _personal_daily(
    limiter: Limiter, game: GameClient, viewer: Viewer, domain: Domain | None
) -> list[DailyStatus] | UpstreamError:
    """Дейлики субъекта; аноним (нет токена) — без вызова game.

    game отвечает анониму 401: статус дейлика есть только у субъекта (гостя или
    пользователя). Фронтенд сначала получает гостевую сессию, затем перезапрашивает блок.
    """
    if viewer.token is None:
        return ANONYMOUS_SKIP
    return await limiter.attempt(game.daily(viewer.token, domain))


async def _personal_records(
    limiter: Limiter, game: GameClient, viewer: Viewer
) -> list[PersonalRecord] | UpstreamError | None:
    """Рекорды пользователя; гостю и анониму рекордов нет (D11) — ``None`` без вызова game.

    Непроверенный токен (JWKS недоступен) уходит в game: гостю он ответит пустым списком.
    """
    if viewer.token is None or viewer.kind is SubjectKind.GUEST:
        return None
    return await limiter.attempt(game.records(viewer.token))


# ----------------------------------------------------------------------------- сборка
def mode_groups(modes: Iterable[Mode], *, exclude_source_types: frozenset[str]) -> list[ModeGroup]:
    """Витрина: режимы, сгруппированные по ``(source_type, presentation)``.

    Группы и режимы внутри них идут в порядке первого появления в ответе game (порядок
    витрины задаёт game). Плановые типы (``exclude_source_types``) в витрину не входят —
    их показывают блоки периода.
    """
    groups: dict[tuple[str, Presentation], list[ModeCard]] = {}
    for mode in modes:
        if mode.source_type in exclude_source_types:
            continue
        groups.setdefault((mode.source_type, mode.presentation), []).append(mode_card(mode))
    return [
        ModeGroup(source_type=source_type, presentation=presentation, modes=cards)
        for (source_type, presentation), cards in groups.items()
    ]


def period_cards(
    modes: Sequence[Mode], statuses: Iterable[DailyStatus], *, weekly_source_types: frozenset[str]
) -> tuple[list[DailyCard], list[DailyCard]]:
    """Статусы ``/v1/daily`` → карточки дейликов и карточки недельных наборов.

    Период определяется типом Source режима: так недельная сетка не попадает к дейликам
    (очков у неё нет, ``max_score`` равен 0), а неизвестный плановый тип по-прежнему
    показывается дейликом, как в этапе 2.
    """
    weekly_keys = {m.key for m in modes if m.source_type in weekly_source_types}
    daily: list[DailyStatus] = []
    weekly: list[DailyStatus] = []
    for status in statuses:
        (weekly if status.mode_key in weekly_keys else daily).append(status)
    return daily_cards(modes, daily), daily_cards(modes, weekly)


def record_cards(modes: Sequence[Mode], records: Iterable[PersonalRecord]) -> list[RecordCard]:
    """Рекорды + режимы → карточки (порядок — как у game); рекорды снятых режимов пропускаются."""
    by_key = {m.key: m for m in modes}
    cards: list[RecordCard] = []
    for record in records:
        mode = by_key.get(record.mode_key)
        if mode is None:
            continue
        cards.append(
            RecordCard(
                mode_key=record.mode_key,
                domain=mode.domain,
                title_key=mode.title_key,
                presentation=mode.presentation,
                best=record.best,
                achieved_at=record.achieved_at,
                percentile=record.percentile,
            )
        )
    return cards


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
                presentation=mode.presentation,
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
        presentation=result.presentation,
        record=result.record,
        bracket=result.bracket,
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
            if exc.status == HTTP_FORBIDDEN and exc.code == GUEST_FORBIDDEN:
                return SubjectView(kind=SubjectKind.GUEST)
            # 401: сессию отозвали после выдачи access-токена — для экрана это аноним.
            return None
        return SubjectView(kind=SubjectKind.USER, display_name=me.display_name)


class GetHomePage:
    """Главная: дейлики и недельные наборы субъекта, все опубликованные режимы."""

    def __init__(
        self,
        game: GameClient,
        *,
        concurrency: int,
        layout: PageLayout | None = None,
        progress: HomeProgress | None = None,
        events: EventsContext | None = None,
    ) -> None:
        self._game, self._concurrency = game, concurrency
        self._layout = layout or PageLayout()
        self._progress = progress
        self._events = events

    async def __call__(self, viewer: Viewer) -> HomePage:
        """Собрать главную."""
        limiter = Limiter(self._concurrency)
        modes, daily, progress, snapshot = await asyncio.gather(
            limiter.attempt(self._game.list_modes()),
            _personal_daily(limiter, self._game, viewer, None),
            self._progress(viewer) if self._progress is not None else _no_progress(),
            self._events.snapshot(viewer) if self._events is not None else _no_snapshot(),
        )
        modes_block = block_of(modes, "modes")
        daily_block = block_of(daily, "dailies")
        dailies, weekly = _period_blocks(
            modes_block.data, daily_block.data, self._layout.weekly_source_types
        )
        return HomePage(
            blocks=HomeBlocks(
                dailies=dailies,
                modes=Block.ok([mode_card(m) for m in self._layout.listed(modes_block.data)])
                if modes_block.data is not None
                else Block.unavailable(),
                weekly=weekly,
                progress=progress,
                event=snapshot.banner if snapshot is not None else None,
            ),
            personal=viewer.is_personal,
        )


async def _no_snapshot() -> None:
    """События не подключены: блок ``event`` - ``null``."""


async def _no_progress() -> None:
    """Прогрессия не подключена: блок ``progress`` — ``null``."""


def _period_blocks(
    modes: Sequence[Mode] | None,
    statuses: Sequence[DailyStatus] | None,
    weekly_source_types: frozenset[str],
) -> tuple[Block[list[DailyCard]], Block[list[DailyCard]]]:
    """Блоки дейликов и недельных наборов: гаснут вместе (один источник ``/v1/daily``)."""
    if modes is None or statuses is None:
        return Block.unavailable(), Block.unavailable()
    daily, weekly = period_cards(modes, statuses, weekly_source_types=weekly_source_types)
    return Block.ok(daily), Block.ok(weekly)


class GetSoloPage:
    """Хаб соло-режимов домена: блоки периода, витрина режимов, личные рекорды."""

    def __init__(
        self, game: GameClient, *, concurrency: int, layout: PageLayout | None = None
    ) -> None:
        self._game, self._concurrency = game, concurrency
        self._layout = layout or PageLayout()

    async def __call__(self, viewer: Viewer, domain: Domain | None) -> SoloPage:
        """Собрать хаб (``domain = None`` — оба домена)."""
        limiter = Limiter(self._concurrency)
        layout = self._layout
        modes, daily, records = await asyncio.gather(
            limiter.attempt(self._game.list_modes()),
            _personal_daily(limiter, self._game, viewer, domain),
            _personal_records(limiter, self._game, viewer),
        )
        modes_block = block_of(modes, "modes")
        daily_block = block_of(daily, "daily")
        in_domain = (
            [m for m in layout.listed(modes_block.data) if domain is None or m.domain == domain]
            if modes_block.data is not None
            else None
        )
        daily_cards_block, weekly = _period_blocks(
            in_domain, daily_block.data, layout.weekly_source_types
        )
        random: Block[list[ModeCard]] = Block.unavailable()
        showcase: Block[list[ModeGroup]] = Block.unavailable()
        if in_domain is not None:
            random = Block.ok(
                [mode_card(m) for m in in_domain if m.source_type in layout.random_source_types]
            )
            showcase = Block.ok(
                mode_groups(in_domain, exclude_source_types=layout.hidden_from_showcase)
            )
        return SoloPage(
            blocks=SoloBlocks(
                daily=daily_cards_block,
                random=random,
                modes=showcase,
                weekly=weekly,
                records=_recordsblock_of(in_domain, records),
            ),
            personal=viewer.is_personal,
        )


def _recordsblock_of(
    modes: Sequence[Mode] | None, records: list[PersonalRecord] | UpstreamError | None
) -> Block[list[RecordCard]] | None:
    """Блок рекордов: ``None`` — гость или аноним; без режимов карточек нет — ``unavailable``."""
    if records is None:
        return None
    block = block_of(records, "records")
    if modes is None or block.data is None:
        return Block.unavailable()
    return Block.ok(record_cards(modes, block.data))


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
            if isinstance(value, UpstreamRejectedError) and value.status == HTTP_NOT_FOUND:
                # Единицу заблокировали после игры — карточки нет, остальные показываем.
                log.info("bff.item_missing", media_item_id=str(item_id))
                continue
            block = block_of(value, "items")
            if block.data is None:
                return Block.unavailable()
            cards.append(item_card(block.data))
        return Block.ok(cards)


def _rejection(exc: UpstreamRejectedError) -> Exception:
    message = "game отклонил запрос"
    if exc.status == HTTP_UNAUTHORIZED:
        return UnauthenticatedError(message, code=exc.code)
    if exc.status == HTTP_FORBIDDEN:
        return ForbiddenError(message, code=exc.code)
    if exc.status == HTTP_CONFLICT:
        return ConflictError(message, code=exc.code)
    # 404 и прочие 4xx — «нет такой сессии»: не раскрываем, почему (IDOR, §9 п.2).
    code = exc.code if exc.status == HTTP_NOT_FOUND else NotFoundError.default_code
    return NotFoundError("сессия не найдена", code=code)
