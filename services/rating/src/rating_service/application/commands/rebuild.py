"""Запрос админа на пересборку лидерборда (выполняет задача ``rating.leaderboards.rebuild``)."""

from __future__ import annotations

from gv_core.clock import Clock
from gv_db.idempotency import request_fingerprint, run_idempotent
from rating_service.application.common import AdminContext, audit
from rating_service.application.ports import Json, UnitOfWorkFactory
from rating_service.domain.errors import LadderNotFoundError, SeasonNotFoundError


class RebuildLeaderboard:
    """Ставит запрос на пересборку лидерборда текущего сезона ladder-а."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, ladder: str, *, ctx: AdminContext) -> Json:
        """Принять запрос.

        Raises:
            LadderNotFoundError: ladder не опубликован.
            SeasonNotFoundError: у ladder-а нет действующего сезона.
        """
        fingerprint = request_fingerprint("leaderboard.rebuild", ladder, {"reason": ctx.reason})
        async with self._uow() as uow:

            async def perform() -> Json:
                if await uow.ladders.published(ladder) is None:
                    raise LadderNotFoundError()
                season = await uow.seasons.active(ladder)
                if season is None:
                    raise SeasonNotFoundError()
                now = self._clock.now()
                await uow.rebuilds.request(ladder, season.season_id, ctx.reason, ctx.actor_id, now)
                await audit(
                    uow, ctx, "leaderboard.rebuild", ladder, now, season_id=str(season.season_id)
                )
                return {"ladder": ladder, "season_id": str(season.season_id), "status": "accepted"}

            response, _ = await run_idempotent(
                uow.idempotency,
                ctx.actor_id,
                ctx.idempotency_key,
                fingerprint,
                perform,
                status_code=202,
            )
            await uow.commit()
        return response
