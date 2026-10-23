"""Сезоны: жизненный цикл, ручные команды админа и периодическая задача (ADR 0039)."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from rating_service.application.commands.leaderboards import STORE_ERRORS, Leaderboards
from rating_service.application.common import AdminContext, audit, season_json
from rating_service.application.ports import (
    Json,
    PublishedLadder,
    RatingUnitOfWork,
    UnitOfWorkFactory,
)
from rating_service.domain.errors import (
    LadderNotFoundError,
    SeasonNotActiveError,
    SeasonNotFoundError,
    SeasonOverlapError,
)
from rating_service.domain.season import ClosedBy, Season, SeasonStatus

log = structlog.get_logger(__name__)


class SeasonLifecycle:
    """Шаги жизненного цикла сезона внутри уже открытой транзакции."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def start(
        self,
        uow: RatingUnitOfWork,
        published: PublishedLadder,
        season: Season | None = None,
        *,
        starts_at: datetime | None = None,
    ) -> Season:
        """Начать сезон: новый (``starts_at``) или плановый ``season``; рейтинги — с мягким сбросом.

        Мягкий сброс применяется, если у ladder-а уже был завершённый сезон.
        """
        config = published.config
        now = self._clock.now()
        previous = await uow.seasons.latest_finished(config.key)
        if season is None:
            season = Season.plan(
                uuid7(),
                config,
                ladder_version=published.version,
                number=await uow.seasons.next_number(config.key),
                starts_at=starts_at or now,
            )
            await uow.seasons.add(season)
        elif season.starts_at > now:
            # Плановый сезон начинается раньше срока (предыдущий закрыт досрочно): длина
            # сезона — по конфигу, поэтому конец пересчитывается.
            season.move_start(now, config)
        if previous is not None:
            await uow.ratings.carry_over(previous.season_id, season.season_id, config, now)
        season.activate(config, reset_applied=previous is not None)
        await uow.seasons.save(season)
        uow.track(season)
        return season

    async def close(
        self, uow: RatingUnitOfWork, published: PublishedLadder, season: Season, closed_by: ClosedBy
    ) -> Season:
        """Закрыть сезон и сразу начать следующий (плановый, если он есть; иначе новый).

        Returns:
            Начатый сезон.
        """
        now = self._clock.now()
        season.finish(now, closed_by, await uow.ratings.count(season.season_id))
        await uow.seasons.save(season)
        uow.track(season)
        planned = [
            s
            for s in await uow.seasons.open_seasons(season.ladder)
            if s.status is SeasonStatus.SCHEDULED
        ]
        return await self.start(uow, published, planned[0] if planned else None)


class SeasonCommands:
    """Сезоны: ручные команды админа и задача расписания."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        lifecycle: SeasonLifecycle,
        boards: Leaderboards,
        *,
        start_skew: timedelta,
        start_max_ahead: timedelta,
    ) -> None:
        self._start_skew = start_skew
        self._start_max_ahead = start_max_ahead
        self._uow = uow
        self._clock = clock
        self._lifecycle = lifecycle
        self._boards = boards

    # ------------------------------------------------------------------ админ
    async def create(self, ladder: str, *, starts_at: datetime | None, ctx: AdminContext) -> Json:
        """Создать сезон: по умолчанию сразу после последнего открытого; длина — из конфига.

        Raises:
            LadderNotFoundError: ladder не опубликован.
            SeasonOverlapError: период пересекается с существующим сезоном.
        """
        fingerprint = request_fingerprint(
            "season.create",
            ladder,
            {"starts_at": starts_at.isoformat() if starts_at else None, "reason": ctx.reason},
        )
        started: Season | None = None
        async with self._uow() as uow:

            async def perform() -> Json:
                nonlocal started
                published = await uow.ladders.published(ladder)
                if published is None:
                    raise LadderNotFoundError()
                now = self._clock.now()
                if starts_at is not None and not (
                    now - self._start_skew <= starts_at <= now + self._start_max_ahead
                ):
                    raise InvalidInputError(
                        "начало сезона вне допустимого окна дат",
                        code="rating.season_start_invalid",
                    )
                open_seasons = await uow.seasons.open_seasons(ladder)
                start = starts_at or max((s.ends_at for s in open_seasons), default=now)
                planned = Season.plan(
                    uuid7(),
                    published.config,
                    ladder_version=published.version,
                    number=await uow.seasons.next_number(ladder),
                    starts_at=start,
                )
                if await uow.seasons.overlaps(ladder, planned.starts_at, planned.ends_at):
                    raise SeasonOverlapError("сезон пересекается с существующим")
                await uow.seasons.add(planned)
                if start <= now and not any(s.status is SeasonStatus.ACTIVE for s in open_seasons):
                    started = await self._lifecycle.start(uow, published, planned)
                    result = started
                else:
                    result = planned
                await audit(uow, ctx, "season.create", ladder, now, season_id=str(result.season_id))
                return season_json(result)

            response, _ = await run_idempotent(
                uow.idempotency,
                ctx.actor_id,
                ctx.idempotency_key,
                fingerprint,
                perform,
                status_code=201,
            )
            await uow.commit()
        if started is not None:
            await self._refresh_boards(started)
        return response

    async def close(self, season_id: UUID, *, ctx: AdminContext) -> Json:
        """Закрыть действующий сезон: мягкий сброс, ``SeasonFinished``, следующий сезон.

        Raises:
            SeasonNotFoundError: нет сезона.
            SeasonNotActiveError: сезон не действует.
        """
        fingerprint = request_fingerprint("season.close", season_id, {"reason": ctx.reason})
        outcome: tuple[Season, Season] | None = None
        async with self._uow() as uow:

            async def perform() -> Json:
                nonlocal outcome
                season = await uow.seasons.get(season_id, for_update=True)
                if season is None:
                    raise SeasonNotFoundError()
                if season.status is not SeasonStatus.ACTIVE:
                    raise SeasonNotActiveError("закрыть можно только действующий сезон")
                published = await uow.ladders.published(season.ladder)
                if published is None:
                    raise LadderNotFoundError()
                next_season = await self._lifecycle.close(uow, published, season, ClosedBy.ADMIN)
                outcome = (season, next_season)
                await audit(
                    uow,
                    ctx,
                    "season.close",
                    season.ladder,
                    self._clock.now(),
                    season_id=str(season_id),
                )
                return season_json(season)

            response, _ = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if outcome is not None:
            await self._refresh_boards(*outcome)
        return response

    # ------------------------------------------------------------------ расписание
    async def tick(self) -> int:
        """Задача расписания: закрыть вышедшие сезоны, начать наступившие и первые.

        Идемпотентна: повтор после сбоя ничего не дублирует. Возвращает число изменений.
        """
        async with self._uow() as uow:
            published = await uow.ladders.all_published()
        changed = 0
        for item in published:
            changed += await self._tick_ladder(item)
        return changed

    async def _tick_ladder(self, published: PublishedLadder) -> int:
        ladder = published.config.key
        now = self._clock.now()
        touched: list[Season] = []
        async with self._uow() as uow:
            for due in await uow.seasons.due(ladder, now):
                locked = await uow.seasons.get(due.season_id, for_update=True)
                if locked is None or not locked.is_due(now):
                    continue
                touched.append(locked)
                touched.append(
                    await self._lifecycle.close(uow, published, locked, ClosedBy.SCHEDULE)
                )
            if await uow.seasons.active(ladder) is None:
                ready = await uow.seasons.scheduled_ready(ladder, now)
                if ready is not None:
                    touched.append(await self._lifecycle.start(uow, published, ready))
                elif not await uow.seasons.open_seasons(ladder):
                    touched.append(await self._lifecycle.start(uow, published))
            await uow.commit()
        if touched:
            await self._refresh_boards(*touched)
        return len(touched)

    async def _refresh_boards(self, *seasons: Season) -> None:
        """Лидерборды затронутых сезонов; сбой Redis не ломает команду (поправит resync)."""
        for season in seasons:
            try:
                await self._boards.rebuild(season.ladder, season.season_id)
            except STORE_ERRORS:
                log.warning("rating.season_board_failed", ladder=season.ladder, exc_info=True)
