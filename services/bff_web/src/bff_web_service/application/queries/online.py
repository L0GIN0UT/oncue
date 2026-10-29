"""Экраны онлайна: хаб и итог матча (план этапа 4, §8; D13, ADR 0017, ADR 0036).

Онлайн — только с аккаунтом (D13): гость и аноним получают на хабе ``auth_required`` без
блоков и без вызовов источников, а на итоге матча — 401/403 как в медиатеке. Каждый блок
деградирует независимо: упавший источник гасит свой блок, страница отвечает 200.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import replace
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    FriendsCard,
    Item,
    ItemCard,
    ItemShelves,
    LadderCard,
    MatchBlocks,
    MatchPage,
    MatchParticipant,
    MatchResult,
    Mode,
    MyMatch,
    MyMatches,
    OnlineBlocks,
    OnlineCountCard,
    OnlineModeCard,
    OnlinePage,
    OnlineStatus,
    OpenRooms,
    PlayerAppearanceCard,
    SeasonCard,
    SubjectKind,
    Viewer,
    WeeklyTopCard,
)
from bff_web_service.application.ports import (
    CatalogClient,
    GameClient,
    LibraryClient,
    MatchClient,
    ProgressionClient,
    RatingClient,
    SocialClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import (
    GUEST_FORBIDDEN,
    HTTP_FORBIDDEN,
    HTTP_UNAUTHORIZED,
    Limiter,
    UpstreamError,
    block_of,
    item_card,
    screen_error,
)
from bff_web_service.application.queries.rooms import fetch_levels, room_title
from bff_web_service.domain import Block
from gv_core.errors import ForbiddenError, UnauthenticatedError

log = structlog.get_logger(__name__)

_MIN_PLAYERS_TO_REPORT = 2
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
    """Хаб онлайна: лобби, мои матчи, режимы, очереди, рейтинг, сезоны и топ недели (4b)."""

    def __init__(
        self,
        match: MatchClient,
        game: GameClient,
        rating: RatingClient,
        *,
        concurrency: int,
        rooms_limit: int,
        matches_limit: int,
        weekly_limit: int,
        social: SocialClient | None = None,
        match_players_limit: int = 0,
        progression: ProgressionClient | None = None,
        room_name_template: str = "Комната {host}",
        levels_batch_size: int = 50,
    ) -> None:
        self._match, self._game, self._rating = match, game, rating
        self._social = social
        self._progression = progression
        self._room_template, self._levels_batch = room_name_template, levels_batch_size
        self._players_limit = match_players_limit
        self._concurrency = concurrency
        self._rooms_limit, self._matches_limit = rooms_limit, matches_limit
        self._weekly_limit = weekly_limit

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
        # Топ недели зависит от списка ladder-ов, но не ждёт остальных блоков: цепочка
        # «ladder-ы → топы» идёт параллельно с первыми вызовами, а не после них.
        (
            rooms,
            matches,
            modes,
            pool,
            ratings,
            (ladders, weekly, friends, online_count),
        ) = await asyncio.gather(
            self._open_rooms(limiter, token),
            limiter.attempt(self._match.my_matches(token, limit=self._matches_limit)),
            limiter.attempt(self._game.online_modes()),
            limiter.attempt(self._match.queue_pool(token)),
            limiter.attempt(self._rating.my_ratings(token)),
            self._rating_and_social(limiter, token),
        )
        personal = (rooms, matches, pool, ratings)
        if any(_is_unauthenticated(v) for v in personal):
            return SESSION_EXPIRED
        if any(_is_guest(v) for v in personal):
            return AUTH_REQUIRED
        modes_block = block_of(modes, "online.modes")
        my_matches = block_of(matches, "online.my_matches")
        if my_matches.data is not None and self._players_limit > 0:
            my_matches = Block.ok(
                await self._with_participants(limiter, token, viewer.user_id, my_matches.data)
            )
        return OnlinePage(
            status=OnlineStatus.OK,
            blocks=OnlineBlocks(
                open_rooms=block_of(rooms, "online.open_rooms"),
                my_matches=my_matches,
                modes=Block.ok(online_mode_cards(modes_block.data))
                if modes_block.data is not None
                else Block.unavailable(),
                queues=block_of(pool, "online.queues"),
                ratings=block_of(ratings, "online.ratings"),
                seasons=_seasons(ladders),
                weekly=weekly,
                friends=friends,
                online_count=online_count,
            ),
        )

    async def _open_rooms(self, limiter: Limiter, token: str) -> OpenRooms | UpstreamError:
        """Открытые лобби с названием и уровнем хоста.

        Уровни хостов берутся у progression после ответа match (нужны id хостов); сбой
        progression оставляет ``host_level = None``, список лобби целый.
        """
        rooms = await limiter.attempt(self._match.open_rooms(token, limit=self._rooms_limit))
        if isinstance(rooms, UpstreamUnavailableError | UpstreamRejectedError):
            return rooms
        levels = await fetch_levels(
            self._progression,
            limiter,
            [r.host_id for r in rooms.items if r.host_id is not None],
            batch_size=self._levels_batch,
        )
        items = [
            replace(
                r,
                title=room_title(r.settings.name, r.host_nickname, self._room_template),
                host_level=levels[r.host_id].level if r.host_id in levels else None,
            )
            for r in rooms.items
        ]
        return replace(rooms, items=items)

    async def _online_count(self, limiter: Limiter) -> Block[OnlineCountCard] | None:
        """Игроков онлайн (social, публичный общий кэш); ``None`` — клиент не подключён."""
        if self._social is None:
            return None
        value = await limiter.attempt(self._social.online_count())
        block = block_of(value, "online.online_count")
        return (
            Block.ok(OnlineCountCard(block.data)) if block.data is not None else Block.unavailable()
        )

    async def _with_participants(
        self, limiter: Limiter, token: str, viewer_id: UUID | None, matches: MyMatches
    ) -> MyMatches:
        """Игроки последних матчей для жалобы из истории (ADR 0069).

        Итог каждого матча — отдельный вызов match (пределом ``match_players_limit`` и общим
        ``Limiter``); сбой одного матча оставляет у его карточки пустой список, хаб цел.
        """
        head = matches.items[: self._players_limit]
        fetched = await asyncio.gather(
            *(limiter.attempt(self._match.match(token, m.match_id)) for m in head)
        )
        enriched: list[MyMatch] = []
        for card, value in zip(head, fetched, strict=True):
            if isinstance(value, MatchResult):
                flagged = with_report_flags(value, viewer_id)
                participants = [
                    MatchParticipant(p.user_id, p.nickname, p.can_report)
                    for p in flagged.players
                    if p.can_report
                ]
            else:
                block_of(value, "online.match_players")  # причина уходит в лог
                participants = []
            enriched.append(replace(card, participants=participants))
        return replace(matches, items=[*enriched, *matches.items[len(head) :]])

    async def _friends(self, limiter: Limiter, token: str) -> Block[FriendsCard] | None:
        """Друзья и число входящих заявок (social); ``None`` — клиент не подключён.

        Любой из двух вызовов не удался — блок ``unavailable``: половина данных (друзья без
        счётчика заявок) вводила бы в заблуждение. Токен social отверг (401/403) —
        тоже только этот блок: сессию хаба решает match.
        """
        if self._social is None:
            return None
        friends, incoming = await asyncio.gather(
            limiter.attempt(self._social.friends(token)),
            limiter.attempt(self._social.incoming_requests(token)),
        )
        if isinstance(friends, list) and isinstance(incoming, int):
            return Block.ok(FriendsCard(friends, incoming))
        for failed in (friends, incoming):
            if isinstance(failed, UpstreamUnavailableError | UpstreamRejectedError):
                block_of(failed, "online.friends")  # причина уходит в лог
                break
        return Block.unavailable()

    async def _rating_and_social(
        self, limiter: Limiter, token: str
    ) -> tuple[
        list[LadderCard] | UpstreamError,
        Block[list[WeeklyTopCard]],
        Block[FriendsCard] | None,
        Block[OnlineCountCard] | None,
    ]:
        """Ladder-ы с топом недели, друзья и счётчик онлайна - независимые цепочки параллельно."""
        (ladders, weekly), friends, online_count = await asyncio.gather(
            self._ladders_and_weekly(limiter, token),
            self._friends(limiter, token),
            self._online_count(limiter),
        )
        return ladders, weekly, friends, online_count

    async def _ladders_and_weekly(
        self, limiter: Limiter, token: str
    ) -> tuple[list[LadderCard] | UpstreamError, Block[list[WeeklyTopCard]]]:
        """Ladder-ы и по ним топ недели (строка ``me`` требует токен, кэш bff не используется)."""
        ladders = await limiter.attempt(self._rating.ladders())
        return ladders, await self._weekly(limiter, token, ladders)

    async def _weekly(
        self, limiter: Limiter, token: str, ladders: list[LadderCard] | UpstreamError
    ) -> Block[list[WeeklyTopCard]]:
        """Топ недели по каждому ladder-у (с токеном: строка ``me``); сбой одного гасит блок."""
        if isinstance(ladders, UpstreamUnavailableError | UpstreamRejectedError):
            return Block.unavailable()
        fetched = await asyncio.gather(
            *(limiter.attempt(self._rating.weekly(x.key, token=token)) for x in ladders)
        )
        tops: list[WeeklyTopCard] = []
        for value in fetched:
            block = block_of(value, "online.weekly")
            if block.data is None:
                return Block.unavailable()
            top = block.data
            tops.append(
                WeeklyTopCard(top.ladder, top.week_start, top.items[: self._weekly_limit], top.me)
            )
        return Block.ok(tops)


def _seasons(ladders: list[LadderCard] | UpstreamError) -> Block[list[SeasonCard]]:
    """Действующие сезоны ladder-ов; ladder без сезона (между сезонами) пропускается."""
    block = block_of(ladders, "online.seasons")
    if block.data is None:
        return Block.unavailable()
    return Block.ok([x.season for x in block.data if x.season is not None])


def online_mode_cards(modes: Sequence[Mode]) -> list[OnlineModeCard]:
    """Онлайн-режимы → карточки создания комнаты (режим без правил комнаты пропускается)."""
    return [
        OnlineModeCard(
            key=m.key,
            version=m.version,
            domain=m.domain,
            title_key=m.title_key,
            source_type=m.source_type,
            clue_type=m.clue_type,
            answer_format=m.answer_format,
            presentation=m.presentation,
            online=m.online,
        )
        for m in modes
        if m.online is not None
    ]


class GetMatchPage:
    """Итог матча: результат из match + карточки единиц раундов (catalog) + флаги медиатеки."""

    def __init__(
        self,
        match: MatchClient,
        catalog: CatalogClient,
        library: LibraryClient,
        progression: ProgressionClient,
        *,
        concurrency: int,
        items_deadline_s: float,
        appearance_batch_size: int = 12,
        items_batch_size: int = 50,
        social: SocialClient | None = None,
        relations_batch_size: int = 12,
    ) -> None:
        self._social = social
        self._relations_batch = relations_batch_size
        self._match, self._catalog, self._library = match, catalog, library
        self._progression = progression
        self._concurrency, self._deadline_s = concurrency, items_deadline_s
        self._batch = appearance_batch_size
        self._items_batch = items_batch_size

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
            return _match_unavailable()
        except UpstreamRejectedError as exc:
            error = screen_error(exc, not_found="матч не найден")
            if error is not None:
                raise error from exc
            log.warning("bff.block_unavailable", block="match", reason=exc.code)
            return _match_unavailable()
        result = with_report_flags(result, viewer.user_id)
        result = await self._with_friend_flags(limiter, token, result)
        ids = _round_item_ids(result)
        # Общий предел обоих блоков: до 50 единиц идут волнами по concurrency, и без него
        # страница ждала бы «волны × timeout_s». Блок, не успевший к сроку, гаснет сам.
        deadline = asyncio.get_running_loop().time() + self._deadline_s
        items, shelves, appearance = await asyncio.gather(
            _until(deadline, self._items(limiter, ids), "match.items"),
            _until(deadline, self._shelves(limiter, token, ids), "match.library"),
            _until(deadline, self._appearance(limiter, token, result), "match.appearance"),
        )
        return MatchPage(
            MatchBlocks(match=Block.ok(result), items=items, library=shelves, appearance=appearance)
        )

    async def _with_friend_flags(
        self, limiter: Limiter, token: str, result: MatchResult
    ) -> MatchResult:
        """``can_add_friend``: social ``can_send_request`` по игрокам с жалобой, пачками.

        Вызывается только для участника несоло-матча (``can_report``): остальным кнопка не
        нужна. Любой сбой social оставляет ``False`` (кнопка скрыта), страница цела.
        """
        if self._social is None:
            return result
        ids = [p.user_id for p in result.players if p.can_report]
        if not ids:
            return result
        batches = _chunks(ids, self._relations_batch)
        fetched = await asyncio.gather(
            *(limiter.attempt(self._social.relations(token, b)) for b in batches)
        )
        addable: set[UUID] = set()
        for value in fetched:
            if isinstance(value, dict):
                addable.update(u for u, r in value.items() if r.can_send_request)
            else:
                block_of(value, "match.friends")  # причина уходит в лог
        return replace(
            result,
            players=[replace(p, can_add_friend=p.user_id in addable) for p in result.players],
        )

    async def _appearance(
        self, limiter: Limiter, token: str, result: MatchResult
    ) -> Block[list[PlayerAppearanceCard]]:
        """Оформление игроков матча пачками (предел источника), дубли схлопнуты."""
        users = list(dict.fromkeys(p.user_id for p in result.players))
        batches = [users[i : i + self._batch] for i in range(0, len(users), self._batch)]
        fetched = await asyncio.gather(
            *(limiter.attempt(self._progression.players_appearance(token, b)) for b in batches)
        )
        cards: list[PlayerAppearanceCard] = []
        for value in fetched:
            if not isinstance(value, list):
                return block_of(value, "match.appearance")
            cards.extend(value)
        # Порядок - по игрокам матча, а не по ответам progression (пачки и их порядок произвольны).
        position = {u: i for i, u in enumerate(users)}
        cards.sort(key=lambda c: position.get(c.user_id, len(users)))
        return Block.ok(cards)

    async def _items(self, limiter: Limiter, ids: list[UUID]) -> Block[list[ItemCard]]:
        """Карточки пачками (предел источника); пропавшие (заблокированы) пропускаем."""
        fetched = await asyncio.gather(
            *(
                limiter.attempt(self._catalog.get_items(chunk))
                for chunk in _chunks(ids, self._items_batch)
            )
        )
        found: dict[UUID, Item] = {}
        for value in fetched:
            block = block_of(value, "match.items")
            if block.data is None:
                return Block.unavailable()
            found.update((i.id, i) for i in block.data)
        # Порядок - по раундам, а не по ответу источника (он не гарантирован).
        cards: list[ItemCard] = []
        for item_id in ids:
            if item_id not in found:
                # Единицу заблокировали после матча — карточки нет, остальные показываем.
                log.info("bff.item_missing", media_item_id=str(item_id))
                continue
            cards.append(item_card(found[item_id]))
        return Block.ok(cards)

    async def _shelves(
        self, limiter: Limiter, token: str, ids: list[UUID]
    ) -> Block[list[ItemShelves]]:
        """Флаги «в медиатеке»: library отдаёт полки пачками (предел источника)."""
        fetched = await asyncio.gather(
            *(
                limiter.attempt(self._library.items_shelves(token, chunk))
                for chunk in _chunks(ids, self._items_batch)
            )
        )
        found: dict[UUID, ItemShelves] = {}
        for value in fetched:
            block = block_of(value, "match.library")
            if block.data is None:
                return Block.unavailable()
            found.update((f.media_item_id, f) for f in block.data)
        # Единицы, которых нет в ответе, - нигде не лежат; порядок - по раундам.
        return Block.ok([found.get(i, ItemShelves(i, [])) for i in ids])


def with_report_flags(result: MatchResult, viewer_id: UUID | None) -> MatchResult:
    """Проставить ``can_report`` игрокам: жалоба на другого участника несоло-матча.

    Решает bff по данным матча (social не вызывается): зритель обязан быть участником,
    сам на себя пожаловаться нельзя, в матче с одним игроком жаловаться не на кого. Субъект
    неизвестен (токен не проверялся локально) — кнопки нет: social всё равно проверит
    право при подаче жалобы.
    """
    ids = {p.user_id for p in result.players}
    if viewer_id is None or viewer_id not in ids or len(ids) < _MIN_PLAYERS_TO_REPORT:
        return result
    return replace(
        result,
        players=[replace(p, can_report=p.user_id != viewer_id) for p in result.players],
    )


def _match_unavailable() -> MatchPage:
    """Итога нет - гаснут все блоки (ids игроков для оформления взять неоткуда)."""
    return MatchPage(
        MatchBlocks(
            Block.unavailable(),
            Block.unavailable(),
            Block.unavailable(),
            Block.unavailable(),
        )
    )


async def _until[T](deadline: float, call: Awaitable[Block[T]], block: str) -> Block[T]:
    """Блок к сроку ``deadline`` (время цикла событий) или ``unavailable``."""
    try:
        async with asyncio.timeout_at(deadline):
            return await call
    except TimeoutError:
        log.warning("bff.block_unavailable", block=block, reason="deadline")
        return Block.unavailable()


def _chunks(ids: list[UUID], size: int) -> list[list[UUID]]:
    """Пачки не больше ``size``."""
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _round_item_ids(result: MatchResult) -> list[UUID]:
    """Единицы раундов без повторов, в порядке раундов."""
    return list(dict.fromkeys(r.media_item_id for r in result.rounds))
