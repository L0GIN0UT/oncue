"""Чтение публичного API rating: ladder-ы, лидерборды, топ недели, рейтинги, история."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NoReturn
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import ForbiddenError, InvalidInputError, UnauthenticatedError
from rating_service.application.commands.leaderboards import Leaderboards
from rating_service.application.common import MASKED_USER, decode_cursor, encode_cursor, tier_json
from rating_service.application.ports import (
    HistoryRow,
    Json,
    LeaderboardStore,
    RatingUnitOfWork,
    UnitOfWorkFactory,
)
from rating_service.domain.errors import (
    LadderNotFoundError,
    MatchNotRatedError,
    NotRankedError,
    SeasonNotFoundError,
    UserNotFoundError,
)
from rating_service.domain.ladder import LadderConfig
from rating_service.domain.rating import Rating
from rating_service.domain.season import Season, SeasonStatus
from rating_service.domain.weekly import week_start

type Viewer = UUID | None
type ViewerKind = Literal["anonymous", "guest", "user"]


def _hidden_tier(config: LadderConfig, matches_total: int) -> bool:
    return config.calibration.hide_tier and config.is_calibrating(matches_total)


class RatingQueries:
    """Запросы публичного API."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        store: LeaderboardStore,
        boards: Leaderboards,
        *,
        max_offset: int,
    ) -> None:
        self._max_offset = max_offset
        self._uow = uow
        self._clock = clock
        self._store = store
        self._boards = boards

    # ------------------------------------------------------------------ ladder-ы
    async def ladders(self) -> Json:
        """Опубликованные ladder-ы с действующим сезоном."""
        async with self._uow() as uow:
            items: list[Json] = []
            for p in await uow.ladders.all_published():
                c = p.config
                season = await uow.seasons.active(c.key)
                items.append(
                    {
                        "key": c.key,
                        "domain": c.domain,
                        "formats": list(c.formats),
                        "version": p.version,
                        "calibration_matches": c.calibration.matches,
                        "tiers": [tier_json(t) for t in c.tiers],
                        "season": _season_view(season),
                    }
                )
        return {"items": items}

    # ------------------------------------------------------------------ лидерборд
    async def leaderboard(
        self,
        ladder: str,
        *,
        viewer: Viewer,
        viewer_kind: ViewerKind,
        cursor: str | None,
        around_me: bool,
        season_id: UUID | None,
        limit: int | None,
    ) -> Json:
        """Страница лидерборда; ``around_me`` — окно вокруг пользователя.

        Raises:
            LadderNotFoundError: ladder не опубликован.
            SeasonNotFoundError: сезон не найден или не начался.
            UnauthenticatedError: ``around=me`` без токена.
            ForbiddenError: ``around=me`` у гостя.
            NotRankedError: игрока нет в лидерборде.
        """
        if around_me:
            if viewer_kind == "anonymous":
                raise UnauthenticatedError("для around=me нужен токен")
            if viewer_kind == "guest":
                raise ForbiddenError("нужен аккаунт", code="auth.guest_forbidden")
        async with self._uow() as uow:
            published = await uow.ladders.published(ladder)
            if published is None:
                raise LadderNotFoundError()
            config = published.config
            season = await self._season(uow, ladder, season_id)
        sid = season.season_id
        await self._boards.ensure(ladder, sid)
        size = await self._store.size(ladder, sid)
        page_size = limit or config.leaderboard.page_size
        me_rank = (
            await self._store.rank(ladder, sid, viewer)
            if viewer is not None and viewer_kind == "user"
            else None
        )
        if around_me:
            if me_rank is None:
                raise NotRankedError()
            offset = max(0, me_rank - page_size // 2)
        else:
            offset = _offset(cursor, self._max_offset)
        rows = await self._store.page(ladder, sid, offset, page_size)
        wanted = [uid for uid, _ in rows]
        if me_rank is not None and viewer is not None:
            wanted.append(viewer)
        async with self._uow() as uow:
            names = await uow.users.nicknames(wanted)
            states = await uow.ratings.many(ladder, sid, wanted)
            # Отставшая запись Redis об удалённом аккаунте не должна светить его id.
            gone = await uow.users.deleted(wanted)

        def entry(place: int, user_id: UUID, rating: int) -> Json:
            state = states.get(user_id)
            masked = user_id in gone
            return {
                "place": place,
                "user_id": str(MASKED_USER if masked else user_id),
                "nickname": "" if masked else names.get(user_id, ""),
                "rating": rating,
                "tier": tier_json(config.tier_for(rating)),
                "matches_played": state.season_matches if state else 0,
            }

        items = [entry(offset + i + 1, uid, rating) for i, (uid, rating) in enumerate(rows)]
        me = None
        if me_rank is not None and viewer is not None and viewer in states:
            me = entry(me_rank + 1, viewer, states[viewer].rating)
        more = offset + len(rows) < size
        return {
            "ladder": ladder,
            "season_id": str(sid),
            "items": items,
            "next_cursor": encode_cursor({"o": offset + len(rows)}) if more else None,
            "me": me,
        }

    async def _season(self, uow: RatingUnitOfWork, ladder: str, season_id: UUID | None) -> Season:
        if season_id is None:
            season = await uow.seasons.active(ladder)
        else:
            season = await uow.seasons.get(season_id)
            if season is not None and (
                season.ladder != ladder or season.status is SeasonStatus.SCHEDULED
            ):
                season = None
        if season is None:
            raise SeasonNotFoundError()
        return season

    # ------------------------------------------------------------------ топ недели
    async def weekly(self, ladder: str, *, viewer: Viewer, viewer_kind: ViewerKind) -> Json:
        """Топ недели: прирост рейтинга с понедельника (зона и размер — из конфига)."""
        async with self._uow() as uow:
            published = await uow.ladders.published(ladder)
            if published is None:
                raise LadderNotFoundError()
            config = published.config
            season = await uow.seasons.active(ladder)
            if season is None:
                raise SeasonNotFoundError()
            week = week_start(self._clock.now(), config.weekly.timezone)
            cal = config.calibration.matches
            top = await uow.weekly.top(
                ladder, week, limit=config.weekly.top_size, calibration_matches=cal
            )
            mine = (
                await uow.weekly.rank_of(ladder, week, viewer, calibration_matches=cal)
                if viewer is not None and viewer_kind == "user"
                else None
            )
            wanted = [r.user_id for r in top] + ([viewer] if mine and viewer else [])
            names = await uow.users.nicknames(wanted)
        items = [
            {
                "place": place,
                "user_id": str(r.user_id),
                "nickname": names.get(r.user_id, ""),
                "delta": r.delta,
            }
            for place, r in enumerate(top, start=1)
        ]
        me = (
            {
                "place": mine[1],
                "user_id": str(viewer),
                "nickname": names.get(viewer, ""),
                "delta": mine[0],
            }
            if mine and viewer
            else None
        )
        return {
            "ladder": ladder,
            "season_id": str(season.season_id),
            "week_start": week.isoformat(),
            "items": items,
            "me": me,
        }

    # ------------------------------------------------------------------ рейтинги игрока
    async def my_ratings(self, user_id: UUID) -> Json:
        """Рейтинги пользователя по ladder-ам в текущем сезоне."""
        return await self._ratings(user_id)

    async def user_ratings(self, user_id: UUID) -> Json:
        """Рейтинги другого пользователя.

        Raises:
            UserNotFoundError: пользователь неизвестен или удалён.
        """
        async with self._uow() as uow:
            if not await uow.users.is_visible(user_id):
                raise UserNotFoundError()
        return await self._ratings(user_id)

    async def _ratings(self, user_id: UUID) -> Json:
        async with self._uow() as uow:
            prepared: list[tuple[LadderConfig, Season, Rating | None]] = []
            for p in await uow.ladders.all_published():
                season = await uow.seasons.active(p.config.key)
                if season is None:
                    continue
                found = await uow.ratings.many(p.config.key, season.season_id, [user_id])
                prepared.append((p.config, season, found.get(user_id)))
        items: list[Json] = []
        for config, season, state in prepared:
            matches_total = state.matches_total if state else 0
            calibrating = config.is_calibrating(matches_total)
            rating = state.rating if state else config.elo.start
            rank = await self._store.rank(config.key, season.season_id, user_id) if state else None
            show_tier = not _hidden_tier(config, matches_total)
            items.append(
                {
                    "ladder": config.key,
                    "season_id": str(season.season_id),
                    "rating": None if calibrating else rating,
                    "tier": tier_json(config.tier_for(rating)) if show_tier else None,
                    "next_tier_at": None if calibrating else config.next_tier_at(rating),
                    "calibrating": calibrating,
                    "calibration_left": config.calibration_left(matches_total),
                    "matches_played": state.season_matches if state else 0,
                    "place": None if rank is None else rank + 1,
                }
            )
        return {"items": items}

    # ------------------------------------------------------------------ история
    async def history(self, user_id: UUID, ladder: str, *, cursor: str | None, limit: int) -> Json:
        """История рейтинга игрока, новые первыми."""
        before = _history_cursor(cursor)
        async with self._uow() as uow:
            published = await uow.ladders.published(ladder)
            if published is None:
                raise LadderNotFoundError()
            rows = await uow.history.page(user_id, ladder, before=before, limit=limit + 1)
        config = published.config
        page = rows[:limit]
        items = [_history_item(config, r) for r in page]
        next_cursor = (
            encode_cursor({"at": page[-1].at.isoformat(), "m": str(page[-1].match_id)})
            if len(rows) > limit
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    async def match_changes(self, match_id: UUID, user_id: UUID) -> Json:
        """Дельты матча для экрана итога; только участник.

        Raises:
            MatchNotRatedError: матч не рейтинговый, не обработан или вызывающий не участник.
        """
        async with self._uow() as uow:
            rows = await uow.history.for_match(match_id)
            if not rows or user_id not in {r.user_id for r in rows}:
                raise MatchNotRatedError()
            published = await uow.ladders.published(rows[0].ladder)
            gone = await uow.users.deleted([r.user_id for r in rows])
        if published is None:
            raise MatchNotRatedError()
        config = published.config
        return {
            "match_id": str(match_id),
            "items": [_change_item(config, r, masked=r.user_id in gone) for r in rows],
        }


# ---------------------------------------------------------------- представления
def _season_view(season: Season | None) -> Json | None:
    if season is None:
        return None
    return {
        "season_id": str(season.season_id),
        "number": season.number,
        "starts_at": season.starts_at.isoformat(),
        "ends_at": season.ends_at.isoformat(),
    }


def _tier_of(config: LadderConfig, key: str | None) -> Json | None:
    tier = next((t for t in config.tiers if t.key == key), None)
    return None if tier is None else tier_json(tier)


def _history_item(config: LadderConfig, r: HistoryRow) -> Json:
    hidden = r.calibrating
    return {
        "match_id": str(r.match_id),
        "at": r.at.isoformat(),
        "format": r.queue_format,
        "outcome": r.outcome,
        "old": None if hidden else r.old,
        "new": None if hidden else r.new,
        "delta": None if hidden else r.delta,
        "tier": _tier_of(config, r.tier),
    }


def _change_item(config: LadderConfig, r: HistoryRow, *, masked: bool) -> Json:
    hidden = r.calibrating
    return {
        "user_id": str(MASKED_USER if masked else r.user_id),
        "ladder": r.ladder,
        "format": r.queue_format,
        "outcome": r.outcome,
        "old": None if hidden else r.old,
        "new": None if hidden else r.new,
        "delta": None if hidden else r.delta,
        "old_tier": _tier_of(config, r.old_tier),
        "tier": _tier_of(config, r.tier),
        "calibrating": r.calibrating,
        "calibration_left": r.calibration_left,
        "leaver_penalty": r.leaver_penalty,
    }


def _offset(cursor: str | None, max_offset: int) -> int:
    if cursor is None:
        return 0
    value = decode_cursor(cursor).get("o")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= max_offset:
        raise_cursor()
    return value


def _history_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    data = decode_cursor(cursor)
    try:
        return datetime.fromisoformat(str(data["at"])), UUID(str(data["m"]))
    except (KeyError, ValueError):
        raise_cursor()


def raise_cursor() -> NoReturn:
    """Неверный курсор → 422 ``cursor.invalid``."""
    raise InvalidInputError("неверный курсор", code="cursor.invalid")
