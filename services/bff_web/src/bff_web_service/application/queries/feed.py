"""Друзья и дейлик: лента главной и блок «среди друзей» итога дейлика.

Общий конвейер ``FriendsDaily``: друзья (social) → результаты текущего периода (game, пачками)
→ видимость статистики (social ``relations``, только для сыгравших) → оформление показанных
(progression, пачками). Агрегирует bff: game дружбы и приватности не знает, social очков тоже.

Приватность: очки друга показываются, только если ``stats_visible`` (скрытая статистика друга
не раскрывается и в счётчике «сыграли»). Чужие очки game отдаёт пользователю с аккаунтом по
явному списку id, поэтому список строится только из друзей зрителя. Любая часть конвейера
не ответила — блок ``unavailable`` целиком (половина данных вводила бы в заблуждение), остальная
страница цела.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    AppearanceCard,
    DailyFeedCard,
    DailyResult,
    DailyStatus,
    Domain,
    FriendDailyCard,
    FriendDailyResult,
    FriendsTodayCard,
    FriendsTodayRow,
    PlayerRelation,
    SessionResult,
    Viewer,
)
from bff_web_service.application.ports import (
    GameClient,
    ProgressionClient,
    SocialClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import Limiter, UpstreamError, block_of
from bff_web_service.application.queries.rating import user_token
from bff_web_service.domain import Block

log = structlog.get_logger(__name__)

_AVERAGE_DIGITS = 1


@dataclass(frozen=True, slots=True)
class FeedLimits:
    """Пределы конвейера друзей (``GV_PAGES__*``, значений в коде нет).

    Attributes:
        scan_limit: Сколько друзей опрашивать на результаты дейлика (первые по списку social).
        results_batch: Игроков в одном вызове game (не больше ``GV_GAME__DAILY_RESULTS_MAX_USERS``).
        relations_batch: Игроков в одном вызове social ``relations`` (предел источника — 50).
        appearance_batch: Игроков в одном вызове progression ``appearance`` (предел — 12).
        deadline_s: Общий срок конвейера целиком; не успел — блок ``unavailable``.
    """

    scan_limit: int
    results_batch: int
    relations_batch: int
    appearance_batch: int
    deadline_s: float


@dataclass(frozen=True, slots=True)
class FriendPlayed:
    """Друг с открытой статистикой и его завершённые дейлики периода."""

    user_id: UUID
    nickname: str
    results: list[DailyResult]


@dataclass(frozen=True, slots=True)
class FriendsSnapshot:
    """Все друзья и те из них, чьи результаты можно показать."""

    total: int
    played: list[FriendPlayed]


def _chunks[T](items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _failure(*values: object) -> UpstreamError | None:
    """Первая ошибка источника среди значений ``gather`` (для ``unavailable`` с причиной в лог)."""
    for value in values:
        if isinstance(value, UpstreamUnavailableError | UpstreamRejectedError):
            return value
    return None


class FriendsDaily:
    """Конвейер «друзья → результаты дейлика → видимость → оформление»."""

    def __init__(
        self,
        social: SocialClient,
        game: GameClient,
        progression: ProgressionClient,
        *,
        concurrency: int,
        limits: FeedLimits,
    ) -> None:
        self._social, self._game, self._progression = social, game, progression
        self._concurrency, self._limits = concurrency, limits

    @property
    def deadline_s(self) -> float:
        """Общий срок конвейера (его же применяют вызывающие вокруг своих добавок)."""
        return self._limits.deadline_s

    async def snapshot(self, limiter: Limiter, token: str) -> FriendsSnapshot | UpstreamError:
        """Друзья зрителя и сыгравшие среди них (с открытой статистикой), лучшие первыми."""
        friends = await limiter.attempt(self._social.friends(token))
        if not isinstance(friends, list):
            return friends
        scanned = friends[: self._limits.scan_limit]
        by_id = {f.user_id: f for f in scanned}
        fetched = await asyncio.gather(
            *(
                limiter.attempt(self._game.daily_results(token, [f.user_id for f in chunk]))
                for chunk in _chunks(scanned, self._limits.results_batch)
            )
        )
        failed = _failure(*fetched)
        if failed is not None:
            return failed
        grouped: dict[UUID, list[DailyResult]] = defaultdict(list)
        for batch in fetched:
            if isinstance(batch, list):
                for result in batch:
                    if result.user_id in by_id:
                        grouped[result.user_id].append(result)
        visible = await self._visible(limiter, token, list(grouped))
        if not isinstance(visible, set):
            return visible
        played = [
            FriendPlayed(
                user_id=u,
                nickname=by_id[u].nickname,
                results=sorted(grouped[u], key=lambda r: (r.domain, r.mode_key)),
            )
            for u in grouped
            if u in visible
        ]
        played.sort(key=_rank)
        return FriendsSnapshot(total=len(friends), played=played)

    async def _visible(
        self, limiter: Limiter, token: str, user_ids: list[UUID]
    ) -> set[UUID] | UpstreamError:
        """Друзья с открытой статистикой (social ``relations``, пачками)."""
        if not user_ids:
            return set()
        fetched = await asyncio.gather(
            *(
                limiter.attempt(self._social.relations(token, chunk))
                for chunk in _chunks(user_ids, self._limits.relations_batch)
            )
        )
        failed = _failure(*fetched)
        if failed is not None:
            return failed
        shown: set[UUID] = set()
        for relations in fetched:
            if isinstance(relations, dict):
                shown.update(u for u, r in relations.items() if _stats_open(r))
        return shown

    async def appearances(
        self, limiter: Limiter, token: str, user_ids: list[UUID]
    ) -> dict[UUID, AppearanceCard] | UpstreamError:
        """Оформление игроков пачками (предел источника), по ``user_id``."""
        if not user_ids:
            return {}
        fetched = await asyncio.gather(
            *(
                limiter.attempt(self._progression.players_appearance(token, chunk))
                for chunk in _chunks(user_ids, self._limits.appearance_batch)
            )
        )
        failed = _failure(*fetched)
        if failed is not None:
            return failed
        cards: dict[UUID, AppearanceCard] = {}
        for batch in fetched:
            if isinstance(batch, list):
                cards.update((p.user_id, p.appearance) for p in batch)
        return cards


def _stats_open(relation: PlayerRelation) -> bool:
    """Статистику друга можно показывать: видима и друг не в блок-листе зрителя."""
    return relation.stats_visible and not relation.blocked_by_me


def _rank(friend: FriendPlayed) -> tuple[int, int, str]:
    """Сначала сыгравшие оба домена, затем по сумме очков, затем по нику."""
    return (-len(friend.results), -sum(r.score for r in friend.results), friend.nickname)


class HomeFeed:
    """Блок ``feed`` главной: результаты друзей по дейликам и счётчик «сыграли из всех»."""

    def __init__(self, pipeline: FriendsDaily, *, concurrency: int, items_limit: int) -> None:
        self._pipeline = pipeline
        self._concurrency, self._items_limit = concurrency, items_limit

    async def __call__(self, viewer: Viewer) -> Block[DailyFeedCard] | None:
        """``None`` — гость или аноним (источники не вызываются); иначе блок."""
        token = user_token(viewer)
        if token is None:
            return None
        limiter = Limiter(self._concurrency)
        try:
            async with asyncio.timeout(self._pipeline.deadline_s):
                return await self._build(limiter, token)
        except TimeoutError:
            log.warning("bff.block_unavailable", block="home.feed", reason="deadline")
            return Block.unavailable()

    async def _build(self, limiter: Limiter, token: str) -> Block[DailyFeedCard]:
        snapshot = await self._pipeline.snapshot(limiter, token)
        if not isinstance(snapshot, FriendsSnapshot):
            block_of(snapshot, "home.feed")  # причина уходит в лог
            return Block.unavailable()
        shown = snapshot.played[: self._items_limit]
        looks = await self._pipeline.appearances(limiter, token, [f.user_id for f in shown])
        if not isinstance(looks, dict):
            block_of(looks, "home.feed")
            return Block.unavailable()
        return Block.ok(
            DailyFeedCard(
                friends_total=snapshot.total,
                friends_played=len(snapshot.played),
                items=[
                    FriendDailyCard(
                        user_id=f.user_id,
                        nickname=f.nickname,
                        results=[
                            FriendDailyResult(r.domain, r.mode_key, r.day, r.score, r.max_score)
                            for r in f.results
                        ],
                        appearance=looks.get(f.user_id),
                    )
                    for f in shown
                ],
            )
        )


class ResultsFriends:
    """Блок ``friends_today`` итога дейлика: друзья в том же режиме и периоде."""

    def __init__(
        self,
        pipeline: FriendsDaily,
        game: GameClient,
        *,
        concurrency: int,
        items_limit: int,
    ) -> None:
        self._pipeline, self._game = pipeline, game
        self._concurrency, self._items_limit = concurrency, items_limit

    async def __call__(
        self, viewer: Viewer, result: SessionResult
    ) -> Block[FriendsTodayCard] | None:
        """``None`` — гость, аноним, не дейлик или период итога уже закончился."""
        token = user_token(viewer)
        if token is None or not result.daily:
            return None
        if result.mode_key is None or result.domain is None or result.day is None:
            return None
        limiter = Limiter(self._concurrency)
        try:
            async with asyncio.timeout(self._pipeline.deadline_s):
                return await self._build(
                    limiter,
                    token,
                    result,
                    mode_key=result.mode_key,
                    domain=result.domain,
                    day=result.day,
                )
        except TimeoutError:
            log.warning("bff.block_unavailable", block="results.friends_today", reason="deadline")
            return Block.unavailable()

    async def _build(
        self,
        limiter: Limiter,
        token: str,
        result: SessionResult,
        *,
        mode_key: str,
        domain: Domain,
        day: date,
    ) -> Block[FriendsTodayCard] | None:
        snapshot, statuses = await asyncio.gather(
            self._pipeline.snapshot(limiter, token),
            limiter.attempt(self._game.daily(token, domain)),
        )
        if not isinstance(snapshot, FriendsSnapshot):
            block_of(snapshot, "results.friends_today")  # причина уходит в лог
            return Block.unavailable()
        if not isinstance(statuses, list):
            block_of(statuses, "results.friends_today")
            return Block.unavailable()
        if _current_day(statuses, mode_key) != day:
            # Game отдаёт результаты друзей только текущего периода: у прошлых периодов
            # сравнения нет, блок не показывается (а не «друзья не играли»).
            return None
        rows = sorted(
            (
                (f, r)
                for f in snapshot.played
                for r in f.results
                if r.mode_key == mode_key and r.day == day
            ),
            key=lambda pair: (-pair[1].score, pair[0].nickname),
        )
        shown = rows[: self._items_limit]
        looks = await self._pipeline.appearances(limiter, token, [f.user_id for f, _ in shown])
        if not isinstance(looks, dict):
            block_of(looks, "results.friends_today")
            return Block.unavailable()
        scores = [r.score for _, r in rows]
        return Block.ok(
            FriendsTodayCard(
                mode_key=mode_key,
                domain=domain,
                day=day,
                my_score=result.score,
                my_max_score=result.max_score,
                my_place=1 + sum(1 for s in scores if s > result.score),
                friends_total=snapshot.total,
                friends_played=len(rows),
                average_score=round(sum(scores) / len(scores), _AVERAGE_DIGITS) if scores else None,
                items=[
                    FriendsTodayRow(
                        user_id=f.user_id,
                        nickname=f.nickname,
                        score=r.score,
                        max_score=r.max_score,
                        appearance=looks.get(f.user_id),
                    )
                    for f, r in shown
                ],
            )
        )


def _current_day(statuses: list[DailyStatus], mode_key: str) -> date | None:
    """Начало текущего периода режима по статусам game."""
    return next((s.day for s in statuses if s.mode_key == mode_key), None)


__all__ = [
    "FeedLimits",
    "FriendPlayed",
    "FriendsDaily",
    "FriendsSnapshot",
    "HomeFeed",
    "ResultsFriends",
]
