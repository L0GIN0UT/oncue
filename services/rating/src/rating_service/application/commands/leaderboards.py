"""Лидерборды: пересборка из Postgres, самолечение, синхронизация игрока (ADR 0039)."""

from __future__ import annotations

from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import AppError, DependencyUnavailableError
from rating_service.application.common import SYSTEM_ACTOR
from rating_service.application.ports import LeaderboardStore, UnitOfWorkFactory
from rating_service.domain.errors import LadderNotFoundError, SeasonNotFoundError
from rating_service.domain.ladder import LadderConfig
from rating_service.domain.rating import Rating
from rating_service.domain.season import SeasonStatus

log = structlog.get_logger(__name__)

# Сбои хранилища чтения не должны останавливать расчёт рейтинга: источник истины — Postgres.
# Адаптер Redis переводит свои ошибки в DependencyUnavailableError.
STORE_ERRORS = (DependencyUnavailableError,)
# «Надгробие» удалённого пользователя: никакая версия `RatingChanged` его не вернёт на доску.
DELETED_VERSION = 2**31 - 1
# Подсказка клиенту, когда доска ещё восстанавливается (секунды до повтора).
RETRY_AFTER_S = 5

type Entry = tuple[UUID, int, int]


def is_on_board(config: LadderConfig, rating: Rating) -> bool:
    """Попадает ли игрок в лидерборд: набрал матчей сезона и вышел из калибровки."""
    return rating.season_matches >= config.leaderboard.min_matches and not config.is_calibrating(
        rating.matches_total
    )


class Leaderboards:
    """Операции над Redis-лидербордами."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: LeaderboardStore,
        clock: Clock,
        *,
        claim_ttl_s: int = 60,
    ) -> None:
        self._uow = uow
        self._store = store
        self._clock = clock
        self._claim_ttl_s = claim_ttl_s

    async def sync(self, config: LadderConfig, rating: Rating, *, deleted: bool = False) -> None:
        """Обновить игрока в лидерборде его сезона.

        Запись версионирована (`rating.event_version`): запоздавшая не затирает новую. Удалённый
        аккаунт на доску не попадает. Сбой Redis — только в лог: поправит resync.
        """
        try:
            if is_on_board(config, rating) and not deleted:
                await self._store.put(
                    rating.ladder,
                    rating.season_id,
                    rating.user_id,
                    rating.rating,
                    rating.event_version,
                )
            else:
                await self._store.remove(
                    rating.ladder, rating.season_id, rating.user_id, rating.event_version
                )
        except STORE_ERRORS:
            log.warning("rating.leaderboard_sync_failed", ladder=rating.ladder, exc_info=True)

    async def forget_user(self, user_id: UUID) -> None:
        """Убрать удалённого пользователя из всех лидербордов."""
        async with self._uow() as uow:
            seasons = await uow.users.rated_seasons(user_id)
        try:
            for ladder, season_id in seasons:
                await self._store.remove(ladder, season_id, user_id, DELETED_VERSION)
        except STORE_ERRORS:
            log.warning("rating.leaderboard_forget_failed", exc_info=True)

    async def _snapshot(self, ladder: str, season_id: UUID) -> tuple[list[Entry], bool]:
        """Игроки доски по состоянию Postgres и признак действующего сезона."""
        async with self._uow() as uow:
            published = await uow.ladders.published(ladder)
            if published is None:
                raise LadderNotFoundError()
            season = await uow.seasons.get(season_id)
            if season is None or season.ladder != ladder:
                raise SeasonNotFoundError()
            config = published.config
            entries = [
                entry
                async for entry in uow.ratings.board(
                    ladder,
                    season_id,
                    min_matches=config.leaderboard.min_matches,
                    calibration_matches=config.calibration.matches,
                )
            ]
            return entries, season.status is SeasonStatus.ACTIVE

    async def rebuild(self, ladder: str, season_id: UUID) -> int:
        """Пересобрать лидерборд сезона из Postgres; число игроков в нём.

        Матч мог закоммититься между чтением БД и записью в Redis, и снимок затёр бы его
        запись. Поэтому после замены БД читается ещё раз и изменившиеся записи дописываются
        версионированно (старее записанного они не станут).

        Raises:
            LadderNotFoundError: ladder не опубликован.
            SeasonNotFoundError: сезона нет в этом ladder-е.
        """
        entries, active = await self._snapshot(ladder, season_id)
        await self._store.replace(ladder, season_id, entries, expires=active)
        again, _ = await self._snapshot(ladder, season_id)
        known = {user_id: (rating, version) for user_id, rating, version in entries}
        for user_id, rating, version in again:
            if known.get(user_id) != (rating, version):
                await self._store.put(ladder, season_id, user_id, rating, version)
        log.info("rating.leaderboard_rebuilt", ladder=ladder, players=len(again))
        return len(again)

    async def ensure(self, ladder: str, season_id: UUID) -> None:
        """Убедиться, что доска есть в Redis; иначе поставить пересборку и ответить 503.

        Публичное чтение не сканирует БД само: запрос в очередь ставится один раз на окно
        `claim_ttl_s` (`SET NX`), а клиент повторяет запрос позже.

        Raises:
            DependencyUnavailableError: доска потеряна и восстанавливается (``retry_after``).
        """
        if await self._store.size(ladder, season_id) > 0 or await self._store.exists(
            ladder, season_id
        ):
            return
        if await self._store.claim_rebuild(ladder, season_id, self._claim_ttl_s):
            async with self._uow() as uow:
                await uow.rebuilds.request(
                    ladder, season_id, "лидерборд потерян в Redis", SYSTEM_ACTOR, self._clock.now()
                )
                await uow.commit()
        raise DependencyUnavailableError(
            "лидерборд восстанавливается", details={"retry_after": RETRY_AFTER_S}
        )

    async def process_requests(self) -> int:
        """Выполнить запросы на пересборку; каждый — в своей транзакции.

        Запрос на несуществующий ladder/сезон закрывается с ``error`` и не мешает остальным;
        недоступный Redis оставляет запрос в очереди до следующего тика.
        """
        async with self._uow() as uow:
            pending = await uow.rebuilds.pending()
        done = 0
        for request in pending:
            error: str | None = None
            try:
                await self.rebuild(request.ladder, request.season_id)
            except STORE_ERRORS:
                log.warning("rating.rebuild_deferred", ladder=request.ladder, exc_info=True)
                break
            except AppError as exc:
                error = exc.code[:200]
                log.warning("rating.rebuild_request_failed", ladder=request.ladder, code=exc.code)
            async with self._uow() as uow:
                await uow.rebuilds.complete(request.request_id, self._clock.now(), error)
                await uow.commit()
            done += 1
        return done

    async def heal(self) -> int:
        """Пересобрать лидерборды действующих сезонов, у которых нет метки готовности."""
        async with self._uow() as uow:
            targets = []
            for published in await uow.ladders.all_published():
                season = await uow.seasons.active(published.config.key)
                if season is not None:
                    targets.append((season.ladder, season.season_id))
        rebuilt = 0
        for ladder, season_id in targets:
            if not await self._store.exists(ladder, season_id):
                await self.rebuild(ladder, season_id)
                rebuilt += 1
        return rebuilt
